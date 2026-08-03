"""Minimal ComfyUI API client -- standard library only, no pip installs.

Submits an API-format workflow to a local ComfyUI server, waits for it to finish,
and reports where the output landed.

Run with the portable install's interpreter:
    python_embeded\\python.exe comfy_client.py --stats
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

DEFAULT_SERVER = "http://127.0.0.1:8188"
HERE = Path(__file__).resolve().parent
DEFAULT_WORKFLOW = HERE / "workflow_api.json"

# Node ids in workflow_api.json. Exported from 1.LTX_2.3_Workflow.json; if the
# graph is ever re-exported after adding or deleting nodes, re-check these.
NODE = {
    "positive": "2483",   # CLIPTextEncode        .text
    "negative": "2612",   # CLIPTextEncode        .text
    "latent": "3059",     # EmptyLTXVLatentVideo  .width .height
    "frames": "4979",     # PrimitiveInt          .value   (drives video AND audio length)
    "fps": "4978",        # PrimitiveFloat        .value
    "seed": "4832",       # RandomNoise           .noise_seed  (distilled branch)
    "save": "4852",       # SaveVideo             .filename_prefix
}


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, server: str = DEFAULT_SERVER, timeout: float = 30.0):
        self.server = server.rstrip("/")
        self.timeout = timeout
        self.client_id = str(uuid.uuid4())

    # ---------- plumbing ----------

    def _get(self, path: str):
        url = f"{self.server}{path}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise ComfyError(f"GET {path} failed: {e}") from e

    def _post(self, path: str, payload: dict):
        url = f"{self.server}{path}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            # ComfyUI returns a structured validation report on 400 -- surface it,
            # it names the offending node and input.
            raise ComfyError(f"POST {path} -> HTTP {e.code}\n{detail}") from e
        except urllib.error.URLError as e:
            raise ComfyError(
                f"POST {path} failed: {e}\nIs ComfyUI running on {self.server}?"
            ) from e

    # ---------- api ----------

    def stats(self) -> dict:
        return self._get("/system_stats")

    def queue(self) -> dict:
        return self._get("/queue")

    def submit(self, workflow: dict) -> str:
        res = self._post("/prompt", {"prompt": workflow, "client_id": self.client_id})
        errors = res.get("node_errors") or {}
        if errors:
            raise ComfyError(f"workflow rejected:\n{json.dumps(errors, indent=2)}")
        pid = res.get("prompt_id")
        if not pid:
            raise ComfyError(f"no prompt_id in response: {res}")
        return pid

    def interrupt(self) -> None:
        self._post("/interrupt", {})

    def _position(self, prompt_id: str):
        """(state, position) where state is 'running' | 'pending' | None."""
        q = self.queue()
        for item in q.get("queue_running", []):
            if len(item) > 1 and item[1] == prompt_id:
                return "running", 0
        for i, item in enumerate(q.get("queue_pending", [])):
            if len(item) > 1 and item[1] == prompt_id:
                return "pending", i + 1
        return None, None

    def wait(
        self,
        prompt_id: str,
        timeout: float = 3600.0,
        poll: float = 3.0,
        quiet: bool = False,
    ) -> dict:
        """Block until the prompt completes. Returns its history entry."""
        started = time.monotonic()
        last = ""
        seen_in_queue = False

        while True:
            hist = self._get(f"/history/{prompt_id}")
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error" or not status.get(
                    "completed", True
                ):
                    msgs = status.get("messages", [])
                    raise ComfyError(
                        f"execution failed:\n{json.dumps(msgs, indent=2)}"
                    )
                if not quiet:
                    print(f"\rdone in {time.monotonic() - started:.0f}s" + " " * 30)
                return entry

            state, pos = self._position(prompt_id)
            if state:
                seen_in_queue = True
            elif seen_in_queue:
                # Left the queue but never reached history: cancelled or crashed.
                raise ComfyError(
                    "prompt left the queue without producing history "
                    "(cancelled, or the server errored -- check its console)"
                )

            elapsed = time.monotonic() - started
            if elapsed > timeout:
                raise ComfyError(f"timed out after {timeout:.0f}s")

            if not quiet:
                where = "running" if state == "running" else f"queued #{pos}"
                line = f"\r{where}  {elapsed:.0f}s elapsed"
                if line != last:
                    sys.stdout.write(line.ljust(len(last)))
                    sys.stdout.flush()
                    last = line
            time.sleep(poll)

    # ---------- outputs ----------

    @staticmethod
    def outputs(entry: dict) -> list[dict]:
        """Every produced file, across output keys ('images', 'videos', ...)."""
        found = []
        for node_id, out in (entry.get("outputs") or {}).items():
            for key, items in out.items():
                if not isinstance(items, list):
                    continue
                for it in items:
                    if isinstance(it, dict) and "filename" in it:
                        found.append({**it, "node_id": node_id, "kind": key})
        return found

    def download(self, item: dict, dest_dir: Path) -> Path:
        q = urllib.parse.urlencode(
            {
                "filename": item["filename"],
                "subfolder": item.get("subfolder", ""),
                "type": item.get("type", "output"),
            }
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / item["filename"]
        with urllib.request.urlopen(f"{self.server}/view?{q}", timeout=300) as r:
            dest.write_bytes(r.read())
        return dest


# ---------- workflow helpers ----------


def load_workflow(path: Path = DEFAULT_WORKFLOW) -> dict:
    if not path.exists():
        raise ComfyError(
            f"{path.name} not found. Export it from ComfyUI with "
            "Workflow -> Export (API), from 1.LTX_2.3_Workflow.json."
        )
    wf = json.loads(path.read_text(encoding="utf-8"))
    if "nodes" in wf:
        raise ComfyError(
            f"{path.name} is in UI format, not API format. "
            "Re-export with Workflow -> Export (API)."
        )
    return wf


def patch(workflow: dict, node_id: str, **inputs) -> dict:
    """Set inputs on a node, in place. Fails loudly on unknown ids/inputs."""
    if node_id not in workflow:
        raise ComfyError(f"node {node_id} not in workflow")
    target = workflow[node_id]["inputs"]
    for k, v in inputs.items():
        if k not in target:
            raise ComfyError(
                f"node {node_id} ({workflow[node_id]['class_type']}) has no "
                f"input {k!r}; available: {sorted(target)}"
            )
        target[k] = v
    return workflow


def describe(workflow: dict) -> str:
    g = lambda key, field: workflow[NODE[key]]["inputs"][field]  # noqa: E731
    return (
        f"{g('latent', 'width')}x{g('latent', 'height')}  "
        f"{g('frames', 'value')} frames @ {g('fps', 'value')} fps "
        f"= {g('frames', 'value') / g('fps', 'value'):.2f}s  "
        f"seed={g('seed', 'noise_seed')}  prefix={g('save', 'filename_prefix')}"
    )


# ---------- cli ----------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    ap.add_argument("--stats", action="store_true", help="show server info and exit")
    ap.add_argument("--show", action="store_true", help="show workflow settings and exit")
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="tiny 320x192 / 25-frame render to prove submit-poll-retrieve",
    )
    ap.add_argument("--timeout", type=float, default=3600.0)
    args = ap.parse_args()

    c = ComfyClient(args.server)

    if args.stats:
        s = c.stats()
        sysinfo, dev = s["system"], s["devices"][0]
        print(f"ComfyUI   {sysinfo['comfyui_version']}  ({sysinfo['deploy_environment']})")
        print(f"python    {sysinfo['python_version'].split()[0]}")
        print(f"torch     {sysinfo['pytorch_version']}")
        # name looks like "cuda:0 NVIDIA GeForce RTX 5060 Ti : cudaMallocAsync"
        gpu = re.sub(r"^\w+:\d+\s*", "", dev["name"]).split(" : ")[0].strip()
        print(f"device    {gpu}")
        print(
            f"vram      {dev['vram_free'] / 2**30:.1f} GiB free "
            f"of {dev['vram_total'] / 2**30:.1f} GiB"
        )
        q = c.queue()
        print(
            f"queue     {len(q.get('queue_running', []))} running, "
            f"{len(q.get('queue_pending', []))} pending"
        )
        return 0

    wf = load_workflow(args.workflow)

    if args.show:
        print(f"{args.workflow.name}: {len(wf)} nodes")
        print(describe(wf))
        saves = [i for i, n in wf.items() if n["class_type"] == "SaveVideo"]
        print(f"SaveVideo nodes: {saves}  (more than one means a double render)")
        return 0

    if args.smoke:
        wf = copy.deepcopy(wf)
        patch(wf, NODE["latent"], width=320, height=192)
        patch(wf, NODE["frames"], value=25)          # 8n+1
        patch(wf, NODE["save"], filename_prefix="smoke")
        print(f"smoke: {describe(wf)}")
        pid = c.submit(wf)
        print(f"prompt_id {pid}")
        entry = c.wait(pid, timeout=args.timeout)
        files = c.outputs(entry)
        if not files:
            print("completed but produced no files", file=sys.stderr)
            return 1
        for f in files:
            print(f"  {f['kind']}: {f['filename']}  (node {f['node_id']})")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ComfyError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
