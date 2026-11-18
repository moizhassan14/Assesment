"""Explicit, opt-in SANDBOX transport for DataForSEO (``DATAFORSEO_MODE=sandbox``).

Why it exists: DataForSEO refuses every request (including its own sandbox host) from an
unverified account (status 40104). The assessment allows "a free-tier/sandbox key ... state
clearly which mode you used". This transport plays the role of DataForSEO's sandbox: it returns
SYNTHETIC data in the documented v3 response shapes so the whole pipeline (LLM planning, tool
validation, retrieval, extraction, scoring, synthesis, UI) can be demonstrated end to end.

Guarantees:
* never the default — the app uses the live API unless DATAFORSEO_MODE=sandbox is set explicitly;
* every run records ``data_mode: "sandbox"``; health/config/report expose it; every synthetic answer
  text starts with "[SANDBOX]" so it cannot be mistaken for a real ChatGPT answer;
* deterministic (hash-seeded) so repeated runs are reproducible; cost is reported as $0.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx

SANDBOX_NOTICE = "Synthetic sandbox data in DataForSEO v3 response shapes — not live measurements."
_GENERIC_DOMAINS = ["g2.com", "capterra.com", "forbes.com", "zapier.com", "pcmag.com", "techradar.com", "reddit.com"]


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


def _envelope(result: list[dict[str, Any]], path: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "sandbox", "status_code": 20000, "status_message": "Ok.", "time": "0.01 sec.", "cost": 0,
        "tasks_count": 1, "tasks_error": 0,
        "tasks": [{"id": f"sandbox-{_seed(path, json.dumps(data, sort_keys=True)):08x}", "status_code": 20000,
                   "status_message": "Ok.", "time": "0.01 sec.", "cost": 0, "result_count": len(result),
                   "path": path.strip("/").split("/"), "data": {"sandbox": True, **data}, "result": result}],
    }


class SandboxTransport(httpx.BaseTransport):
    """Answers DataForSEO endpoints locally. Brand/competitor domains are taken from the run's request tag."""

    def __init__(self, entities: dict[str, list[dict[str, str]]] | None = None) -> None:
        self.entities = entities or {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        task = json.loads(request.content or b"[{}]")[0]
        if path.endswith("/keyword_ideas/live"):
            result = self._keyword_ideas(task)
        elif path.endswith("/keyword_overview/live"):
            result = self._keyword_overview(task)
        elif path.endswith("/organic/live/advanced"):
            result = self._serp(task)
        elif path.endswith("/llm_responses/live"):
            result = self._llm(task)
        else:
            return httpx.Response(404, json={"status_code": 40400, "status_message": "Not Found (sandbox)."})
        return httpx.Response(200, json=_envelope(result, path, {k: v for k, v in task.items() if k != "tag"}))

    # -------------------------------------------------------------------- generators
    @staticmethod
    def _metrics(keyword: str) -> dict[str, Any]:
        s = _seed("kw", keyword)
        volume = [90, 260, 590, 1300, 2900, 6600, 14800, 33100][s % 8]
        comp = round((s >> 4) % 100 / 100, 2)
        return {
            "keyword": keyword,
            "keyword_info": {"search_volume": volume, "competition": comp,
                             "competition_level": "HIGH" if comp > 0.66 else "MEDIUM" if comp > 0.33 else "LOW",
                             "cpc": round(1 + (s >> 8) % 2500 / 100, 2)},
            "keyword_properties": {"keyword_difficulty": (s >> 12) % 90 + 5},
            "search_intent_info": {"main_intent": ["commercial", "informational", "transactional"][(s >> 16) % 3]},
        }

    def _keyword_ideas(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        seeds = [str(k) for k in task.get("keywords", [])]
        variants: list[str] = []
        for seed in seeds:
            variants += [seed, f"best {seed}", f"{seed} for small teams", f"free {seed}", f"{seed} comparison"]
        items = [self._metrics(k) for k in list(dict.fromkeys(variants))[: int(task.get("limit", 20))]]
        return [{"se_type": "google", "seed_keywords": seeds, "total_count": len(items), "items_count": len(items), "items": items}]

    def _keyword_overview(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        items = [self._metrics(str(k)) for k in task.get("keywords", [])]
        return [{"se_type": "google", "items_count": len(items), "items": items}]

    def _run_entities(self, task: dict[str, Any]) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
        ents = self.entities.get(str(task.get("tag", "")), [])
        brand = next((e for e in ents if e.get("is_brand") == "1"), None)
        return brand, [e for e in ents if e.get("is_brand") != "1"]

    def _serp(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        keyword = str(task.get("keyword", ""))
        brand, competitors = self._run_entities(task)
        s = _seed("serp", keyword)
        domains = [c["domain"] for c in competitors] + _GENERIC_DOMAINS
        start = s % max(1, len(domains))
        ordered = domains[start:] + domains[:start]
        if brand is not None and s % 3 != 0:  # brand ranks on ~2/3 of queries, at a hash-chosen position
            ordered.insert(3 + (s >> 4) % 6, brand["domain"])
        items: list[dict[str, Any]] = []
        if s % 2 == 0:
            refs = [{"source": d, "domain": d, "url": f"https://{d}/sandbox/{keyword.replace(' ', '-')}", "title": f"{keyword} (sandbox)"}
                    for d in ordered[:3]]
            items.append({"type": "ai_overview", "rank_group": 1, "rank_absolute": 1,
                          "markdown": f"[SANDBOX] Synthetic AI Overview for '{keyword}'.",
                          "items": [{"type": "ai_overview_element", "text": "[SANDBOX] Synthetic overview text.", "references": refs}]})
        for rank, domain in enumerate(ordered[: int(task.get("depth", 10))], start=1):
            items.append({"type": "organic", "rank_group": rank, "rank_absolute": rank + len(items), "domain": domain,
                          "url": f"https://{domain}/sandbox/{keyword.replace(' ', '-')}", "title": f"{keyword} — {domain} (sandbox)"})
        return [{"keyword": keyword, "type": "organic", "items_count": len(items), "item_types": sorted({i["type"] for i in items}),
                 "items": items}]

    def _llm(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = str(task.get("user_prompt", ""))
        brand, competitors = self._run_entities(task)
        s = _seed("llm", prompt)
        named = [c["name"] for c in competitors][:2]
        if brand is not None and s % 2 == 0:
            named.append(brand["name"])
        cited = [c["domain"] for c in competitors][:1] + _GENERIC_DOMAINS[:1]
        text = (f"[SANDBOX] Synthetic answer (not from ChatGPT) to: '{prompt[:120]}'. Options frequently compared: "
                f"{', '.join(named) if named else 'various vendors'}.")
        annotations = [{"title": d, "url": f"https://{d}/sandbox", "start_index": 0, "end_index": 10, "text": d} for d in cited]
        return [{"model_name": f"{task.get('model_name', 'gpt-4o-mini')} (sandbox)", "input_tokens": 0, "output_tokens": 0,
                 "web_search": bool(task.get("web_search")), "money_spent": 0, "datetime": "2026-11-18 00:00:00 +00:00",
                 "items": [{"type": "message", "sections": [{"type": "text", "text": text, "annotations": annotations}]}],
                 "fan_out_queries": [], "search_results": []}]
