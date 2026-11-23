"""The VLM service (server side): Astra (OpenAI Responses API, raw HTTP as panda_twin's astra_e2e.py) for a skill
program, which never has the key or the network. Per episode at most `max_calls` (1) calls; every call checked
against hard spending caps (one ledger for all runs: runs/budget/openai.jsonl, file-locked):

  total_usd       configs/models.yaml budget.total_usd      (all spending of this repository, every run)
  run_usd         the experiment's vlm.run_usd               (one learning run / suite, by its tag)
  episode_usd     the experiment's vlm.max_usd_per_episode   (reserved before the call, settled after)
Costs from the reply's token usage and configs/models.yaml's price table. An exact-request cache (sha256 of the request)
can serve repeated identical requests (dev comparisons: the same first frames for candidate and incumbent): a hit costs
nothing and is recorded as one.

respond(): the VLM baselines' multi-turn calls (the Responses API's conversation: instructions, function tools,
previous_response_id), allowed only when the vlm block says converse: true (max_calls_per_episode calls, each one
checked against the caps above; max_usd_per_episode is then the episode's total). Never cached."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np

from .. import paths

LEDGER = paths.RUNS / "budget" / "openai.jsonl"


class BudgetExceeded(RuntimeError):
    pass


def models_config():
    return paths.load_yaml("configs/models.yaml")


def _lock():
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    f = open(LEDGER.parent / "openai.lock", "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def spent(tag_prefix=None):
    """All spending, or one run's: the tags whose part before ':' is tag_prefix (exactly: run X's 'X:practice' and
    'X:dev', not 'X-heldout:...' or 'X-dev:dev', which are budgets of their own)."""
    if not LEDGER.exists():
        return 0.0
    s = 0.0
    for line in open(LEDGER):
        r = json.loads(line)
        if tag_prefix is None or str(r.get("tag", "")).split(":")[0] == tag_prefix:
            s += float(r.get("usd", 0.0))
    return s


def _record(**rec):
    with open(LEDGER, "a") as f:
        f.write(json.dumps(dict(rec, t=time.time())) + "\n")


def png_item(rgb, detail="high"):
    ok, b = cv2.imencode(".png", cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR))
    return {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(b.tobytes()).decode(), "detail": detail}


class VLM:
    def __init__(self, cfg, tag, cache_dir=None):
        """cfg: the experiment's vlm block (enabled, model key in configs/models.yaml, max_calls_per_episode,
        max_usd_per_episode, run_usd); tag: the ledger tag (run / suite); cache_dir: exact-request cache or None."""
        M = models_config()
        self.m = M["models"][cfg.get("model", "astra")]
        self.total_cap = float(M["budget"]["total_usd"])
        self.cfg, self.tag = cfg, tag
        self.max_calls = int(cfg.get("max_calls_per_episode", 1))
        self.ep_cap = float(cfg.get("max_usd_per_episode", 0.25))
        self.run_cap = float(cfg.get("run_usd", 0.0))
        self.cache = Path(cache_dir) if cache_dir else None
        self.calls, self.usd, self.wait_s, self.tokens, self.hits = 0, 0.0, 0.0, dict(input=0, cached=0, output=0, reasoning=0), 0
        self.log = []

    def key(self):
        f = (paths.local().get("secrets") or {}).get("openai_key_file")
        if not f:
            raise RuntimeError("configs/local.yaml secrets.openai_key_file is not set")
        return Path(f).expanduser().read_text().strip()

    def ask(self, prompt, images, detail="high", effort=None, max_output_tokens=None):
        """-> dict(ok, text, usd, wait_s, tokens, cache_hit) or dict(ok=False, why). effort, max_output_tokens: the
        reasoning effort and the reply's limit, reasoning included (default: the model's in configs/models.yaml)."""
        if self.calls >= self.max_calls:
            return dict(ok=False, why=f"at most {self.max_calls} model call(s) per episode")
        body = {"model": self.m["model"], "reasoning": {"effort": effort or self.m.get("effort", "medium")},
                "max_output_tokens": int(max_output_tokens or self.m.get("max_output_tokens", 6000)),
                "input": [{"role": "user", "content": [{"type": "input_text", "text": str(prompt)}] +
                           [png_item(im, detail) for im in images]}]}
        h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        self.calls += 1
        if self.cache is not None and (self.cache / f"{h}.json").exists():
            j = json.loads((self.cache / f"{h}.json").read_text()); self.hits += 1
            _record(tag=self.tag, usd=0.0, cache_hit=True, request=h[:16])
            return dict(ok=True, text=_text(j), usd=0.0, wait_s=0.0, tokens={}, cache_hit=True)
        with _lock():                                          # reserve the episode cap against every cap
            tot, run = spent(), spent(self.tag.split(":")[0])
            if tot + self.ep_cap > self.total_cap:
                return dict(ok=False, why=f"model budget: ${tot:.2f} of ${self.total_cap:.2f} total spent")
            if self.run_cap and run + self.ep_cap > self.run_cap:
                return dict(ok=False, why=f"model budget: ${run:.2f} of this run's ${self.run_cap:.2f} spent")
            _record(tag=self.tag, usd=self.ep_cap, reserve=True, request=h[:16])
        import requests
        t0 = time.time(); err = None; j = None
        for attempt in range(3):
            try:
                r = requests.post("https://api.openai.com/v1/responses", json=body, timeout=180,
                                  headers={"Authorization": f"Bearer {self.key()}"})
                if r.status_code == 200:
                    j = r.json(); break
                err = f"{r.status_code}: {r.text[:300]}"
                if r.status_code in (400, 401, 403, 404):
                    break
            except requests.RequestException as e:
                err = repr(e)[:300]
            time.sleep(4 * 2 ** attempt)
        wait = time.time() - t0
        self.wait_s += wait
        u = (j or {}).get("usage") or {}
        pin, out = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        cached = int((u.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0)
        reas = int((u.get("output_tokens_details") or {}).get("reasoning_tokens", 0) or 0)
        P = self.m["price_per_mtok"]
        usd = ((pin - cached) * P["in"] + cached * P["cached"] + out * P["out"]) / 1e6
        with _lock():                                          # settle: release the reservation, book the cost
            _record(tag=self.tag, usd=-self.ep_cap, reserve=False, request=h[:16])
            _record(tag=self.tag, usd=usd, model=self.m["model"], tokens_in=pin, cached=cached, tokens_out=out,
                    reasoning=reas, wait_s=round(wait, 2), ok=j is not None, request=h[:16])
        self.usd += usd
        for k_, v in (("input", pin), ("cached", cached), ("output", out), ("reasoning", reas)):
            self.tokens[k_] += v
        if j is None:
            return dict(ok=False, why=f"model call failed: {err}", usd=usd, wait_s=round(wait, 2))
        if self.cache is not None:
            self.cache.mkdir(parents=True, exist_ok=True)
            (self.cache / f"{h}.json").write_text(json.dumps(j))
        return dict(ok=True, text=_text(j), usd=round(usd, 5), wait_s=round(wait, 2),
                    tokens=dict(input=pin, cached=cached, output=out, reasoning=reas), cache_hit=False)

    def respond(self, input, images, instructions=None, tools=None, previous=None, effort=None, max_output_tokens=None, extra=None,
                timeout_s=600):
        """One turn of a conversation. input: Responses API input items; an image is {"type": "input_image",
        "image": k} -> images[k] (RGB array) as a PNG. -> dict(ok, id, status, output (the reply's items), usd, wait_s,
        tokens) or dict(ok=False, why). extra: more fields of the request's body; timeout_s: of one HTTP request."""
        if not self.cfg.get("converse"):
            return dict(ok=False, why="no multi-turn model calls in this run")
        if self.calls >= self.max_calls:
            return dict(ok=False, why=f"at most {self.max_calls} model calls per episode")
        if self.usd >= self.ep_cap:
            return dict(ok=False, why=f"this episode's model budget (${self.ep_cap:.2f}) is spent")

        def fill(x):
            if isinstance(x, dict):
                if x.get("type") == "input_image" and "image" in x:
                    return png_item(images[int(x["image"])], x.get("detail", "high"))
                return {k: fill(v) for k, v in x.items()}
            if isinstance(x, list):
                return [fill(v) for v in x]
            return x
        body = {"model": self.m["model"], "input": fill(list(input)), "parallel_tool_calls": False,
                "reasoning": {"effort": effort or self.m.get("effort", "medium")},
                "max_output_tokens": int(max_output_tokens or self.m.get("max_output_tokens", 6000))}
        if instructions:
            body["instructions"] = str(instructions)
        if tools:
            body["tools"] = tools
        if previous:
            body["previous_response_id"] = str(previous)
        body.update(extra or {})
        h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        self.calls += 1
        res = float(self.cfg.get("max_usd_per_call", min(self.ep_cap, 0.25)))
        with _lock():
            tot, run = spent(), spent(self.tag.split(":")[0])
            if tot + res > self.total_cap:
                return dict(ok=False, why=f"model budget: ${tot:.2f} of ${self.total_cap:.2f} total spent")
            if self.run_cap and run + res > self.run_cap:
                return dict(ok=False, why=f"model budget: ${run:.2f} of this run's ${self.run_cap:.2f} spent")
            _record(tag=self.tag, usd=res, reserve=True, request=h[:16])
        import requests
        t0 = time.time(); err = None; j = None
        for attempt in range(4):
            try:
                r = requests.post("https://api.openai.com/v1/responses", json=body, timeout=timeout_s,
                                  headers={"Authorization": f"Bearer {self.key()}"})
                if r.status_code == 200:
                    j = r.json(); break
                err = f"{r.status_code}: {r.text[:300]}"
                if r.status_code in (400, 401, 403, 404):
                    break
            except requests.RequestException as e:
                err = repr(e)[:300]
            time.sleep(5 * 2 ** attempt)
        wait = time.time() - t0
        self.wait_s += wait
        u = (j or {}).get("usage") or {}
        pin, out = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        cached = int((u.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0)
        reas = int((u.get("output_tokens_details") or {}).get("reasoning_tokens", 0) or 0)
        P = self.m["price_per_mtok"]
        usd = ((pin - cached) * P["in"] + cached * P["cached"] + out * P["out"]) / 1e6
        with _lock():
            _record(tag=self.tag, usd=-res, reserve=False, request=h[:16])
            _record(tag=self.tag, usd=usd, model=self.m["model"], tokens_in=pin, cached=cached, tokens_out=out,
                    reasoning=reas, wait_s=round(wait, 2), ok=j is not None, request=h[:16], turn=True)
        self.usd += usd
        for k_, v in (("input", pin), ("cached", cached), ("output", out), ("reasoning", reas)):
            self.tokens[k_] += v
        if j is None:
            return dict(ok=False, why=f"model call failed: {err}", usd=round(usd, 5), wait_s=round(wait, 2))
        return dict(ok=True, id=j.get("id"), status=j.get("status"), output=j.get("output") or [], usd=round(usd, 5),
                    wait_s=round(wait, 2), tokens=dict(input=pin, cached=cached, output=out, reasoning=reas))

    def usage(self):
        return dict(model_calls=self.calls, model_cost_usd=round(self.usd, 5), model_wait_s=round(self.wait_s, 2),
                    model_tokens=self.tokens, model_cache_hits=self.hits, model=self.m["model"])


def _text(j):
    out = []
    for it in j.get("output") or []:
        for c in it.get("content") or []:
            if c.get("type") in ("output_text", "text"):
                out.append(c.get("text", ""))
    return "\n".join(out) if out else (j.get("output_text") or "")
