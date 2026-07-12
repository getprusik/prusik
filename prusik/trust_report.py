"""Adopter TRUST REPORT (roadmap Horizon-2 D) — a per-repo dossier of what prusik DID for
THIS adopter, derived entirely from the adopter's OWN ledger plus a live fidelity probe of
their config. Adopter-facing: it ships in the wheel (unlike HQ's fleet/eng dashboards,
which are HQ-only). It converts prusik's internal evidence into adopter-visible ROI.

Honest by construction (prusik's own bar): a number is shown only when the ledger
evidences it; '–' otherwise. A computed VERDICT (never marketing) leads, then five
sections, each a distinct kind of value:

  1. FIDELITY      — a live divergence-injection probe of the adopter's config: do the
                     deterministic gates still catch the 3 failure modes, with no control
                     falsely blocked. "The harness verifying you is itself verified, here."
  2. VERIFICATION  — what prusik CAUGHT with proof: evidence-gate + critic true-catches
                     (these carry a precision — they are the measured high-value layer).
  3. RECALL        — the honest half: what the gates MISS. Critic catches vs confirmed
                     misses (an UPPER BOUND while candidates pend — a clean number can't
                     hide an unrecorded miss), plus the out-of-diff detectors and their
                     DERIVED precision (advisory, earned not asserted). The differentiator
                     a self-reporting agent can't make: measuring its own blind spots.
  4. PREVENTION    — what prusik BLOCKED before it shipped: writable/phase-gate + rewind
                     activity. Shown as COUNTS, never precision: a preventive control's
                     value is the divergence it prevents (invisible by construction), so a
                     "precision" on it would mis-signal (the strategic-posture caveat).
  5. THROUGHPUT    — sprints completed / started: the autonomous work that shipped.
"""

from __future__ import annotations

import html
import json
import tempfile
from pathlib import Path
from typing import Any

from prusik import catch_quality, ledger

# Verification gates carry a meaningful precision (caught a fabricated/wrong claim).
_VERIFICATION_GATES = ("evidence_gate", "critic")
# Preventive gates: value = prevention, NOT catch-precision — shown as activity only.
_PREVENTIVE_GATES = ("writable_gate", "phase_gate", "rewind")


def compose(records: list[dict], config: dict | None = None) -> dict[str, Any]:
    """Build the trust-report data from the adopter's ledger + a live fidelity probe of
    their `config` (the loaded sprint-config, or None)."""
    fidelity: dict[str, Any] | None = None
    if config:
        from prusik import injection
        with tempfile.TemporaryDirectory() as td:
            results = injection.run_cases(config, Path(td))
        s = injection.summarize(results)
        fidelity = {
            "caught": s["catch_rate"],              # [caught, total]
            "discrimination": s["discrimination"],  # [unflagged-controls, total]
            "misses": [m["id"] for m in s["misses"]],
            "false_blocks": [m["id"] for m in s["false_blocks"]],
        }

    catches = catch_quality.resolve_catches(
        catch_quality.extract_catches(records), records)
    cq = catch_quality.summarize(catches)
    verification = {g: cq[g] for g in _VERIFICATION_GATES if g in cq}
    prevention = {g: cq[g] for g in _PREVENTIVE_GATES if g in cq}

    completed = sum(1 for r in records if r.get("event") == "sprint_complete")
    started = sum(1 for r in records if r.get("event") == "sprint_started")

    rep: dict[str, Any] = {
        "fidelity": fidelity,
        "verification": verification,
        "recall": _recall_section(records, cq),
        "prevention": prevention,
        "throughput": {"sprints_completed": completed, "sprints_started": started},
        "total_fires": len(catches),
    }
    rep["verdict"] = _verdict(rep)
    return rep


# Out-of-diff recall detectors — advisory gates whose precision is DERIVED (v0.177),
# so the dossier reports an EARNED number, never a marketed one.
_RECALL_DETECTORS = ("absence_detector", "narrative_detector", "delta_detector")


def _recall_section(records: list[dict], cq: dict[str, Any]) -> dict[str, Any]:
    """The other half of catch-quality: what the gates MISS. Precision says a flag is
    trustworthy; recall says coverage. A miss leaves no trail (the critic passed), so
    recall is inferred from downstream catches and reported as an UPPER BOUND while
    candidates pend — never a clean all-clear. Plus the out-of-diff detectors and their
    earned precision."""
    from prusik import critic_recall
    rc = critic_recall.recall_summary(records)
    return {
        "catches": rc["catches"],
        "confirmed_misses": rc["misses"],
        "recall_pct": rc["recall_pct"],
        "is_upper_bound": rc["is_upper_bound"],
        "pending_candidates": rc["pending_candidates"],
        "detectors": {g: cq[g] for g in _RECALL_DETECTORS if g in cq},
    }


def _verdict(rep: dict[str, Any]) -> str:
    """A computed, earned one-line decision summary — never marketing."""
    f = rep["fidelity"]
    if f is None:
        return "run `prusik init`, then re-run this report to see the proof on your code"
    if f["misses"] or f["false_blocks"]:
        return ("⚠ on your code, a safeguard missed or wrongly blocked a planted test "
                "defect — resolve this before relying on automated delivery")
    c, t = f["caught"]
    ver = rep["verification"]
    tt = sum(v[catch_quality.TRUE_CATCH] for v in ver.values())
    res = sum(v[catch_quality.TRUE_CATCH] + v[catch_quality.FALSE_BLOCK]
              for v in ver.values())
    prec_txt = (f"{round(100 * tt / res)}% of its alerts were real problems"
                if res else "with no false alarms")
    return (f"✓ on your code: it caught all {c} of {t} planted defects, {prec_txt}, "
            f"and it openly measures what it might miss — every figure proven from the "
            f"record of what happened, not the system's own say-so")


def _pct(precision: float | None) -> str:
    return "–" if precision is None else f"{100 * precision:.0f}%"


def render_text(rep: dict[str, Any]) -> str:
    from prusik import __version__
    out: list[str] = []
    out.append(f"prusik TRUST REPORT — what the harness did for this repo "
               f"(prusik {__version__})")
    out.append("")
    out.append(f"  VERDICT         : {rep['verdict']}")
    out.append("")

    f = rep["fidelity"]
    if f is None:
        out.append("  1. FIDELITY     : – (no sprint-config — run `prusik init`)")
    else:
        c, t = f["caught"]
        dc, dt = f["discrimination"]
        ok = not f["misses"] and not f["false_blocks"]
        out.append(f"  1. FIDELITY     : {'✓' if ok else '✗'} your gates catch "
                   f"{c}/{t} injected divergences · {dc}/{dt} controls not falsely "
                   f"blocked — the verifier, verified against your config")
        if f["misses"]:
            out.append(f"                    ✗ MISSED: {', '.join(f['misses'])}")
        if f["false_blocks"]:
            out.append(f"                    ✗ FALSE-BLOCKED: {', '.join(f['false_blocks'])}")

    ver = rep["verification"]
    if ver:
        out.append("  2. VERIFICATION : caught with proof (precision = true / resolved):")
        for g in sorted(ver, key=lambda g: -ver[g]["fired"]):
            s = ver[g]
            out.append(f"       {g:14s} {s['fired']:3d} fired · "
                       f"{s[catch_quality.TRUE_CATCH]:3d} true-catch · {_pct(s['precision'])}")
    else:
        out.append("  2. VERIFICATION : – (no evidence-gate / critic fires yet)")

    rec = rep["recall"]
    bound = (f" (upper bound — {rec['pending_candidates']} candidate(s) pending "
             f"lower it)" if rec["is_upper_bound"] else "")
    rp = "–" if rec["recall_pct"] is None else f"{rec['recall_pct']}%"
    out.append(f"  3. RECALL       : the honest half — what the gates MISS. "
               f"{rec['catches']} caught · {rec['confirmed_misses']} confirmed-missed "
               f"· recall {rp}{bound}")
    if rec["detectors"]:
        out.append("                    out-of-diff detectors (advisory; precision "
                   "earned, not asserted):")
        for g in sorted(rec["detectors"], key=lambda g: -rec["detectors"][g]["fired"]):
            s = rec["detectors"][g]
            out.append(f"       {g:18s} {s['fired']:3d} flagged · "
                       f"{s[catch_quality.TRUE_CATCH]:3d} true · {_pct(s['precision'])}")

    pre = rep["prevention"]
    if pre:
        out.append("  4. PREVENTION   : divergences blocked before they shipped. Counts, "
                   "not precision — but where the ledger bears it out, an ENFORCED count "
                   "(the block forced a real requirement that was then produced):")
        for g in sorted(pre, key=lambda g: -pre[g]["fired"]):
            s = pre[g]
            tc = s[catch_quality.TRUE_CATCH]
            suffix = (f" · {tc} enforced (blocked transition later succeeded)"
                      if tc else "")
            out.append(f"       {g:14s} {s['fired']:3d} blocked{suffix}")
    else:
        out.append("  4. PREVENTION   : – (no writable/phase/rewind blocks yet)")

    th = rep["throughput"]
    out.append(f"  5. THROUGHPUT   : {th['sprints_completed']} sprint(s) completed "
               f"/ {th['sprints_started']} started")
    out.append("")
    out.append("  Honest by construction: every number is ledger-derived; '–' means the "
               "ledger doesn't yet evidence it (no claim without proof).")
    return "\n".join(out)


_CSS = """
:root{
 --bg:#0a0c11;--panel:#13171f;--line:#222a36;--line2:#323c4b;
 --text:#e8eef6;--muted:#93a0b2;--dim:#5e6b7c;
 --good2:#56d364;--warn:#d6a533;--bad:#f85149;--accent:#6cb0ff;
 --elev:inset 0 1px 0 rgba(255,255,255,.05),0 1px 2px rgba(0,0,0,.35),0 16px 36px -16px rgba(0,0,0,.6);}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 620px at 50% -220px,#10151f 0%,var(--bg) 58%);
 color:var(--text);font:15px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.page{max-width:900px;margin:0 auto;padding:0 28px 72px}
code{font:12.5px/1 ui-monospace,SFMono-Regular,Menlo,monospace;background:rgba(255,255,255,.06);
 padding:1px 5px;border-radius:5px;color:var(--text)}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:22px 2px;border-bottom:1px solid var(--line)}
.brand{font-weight:700;letter-spacing:-.01em;font-size:15.5px}
.brand .s{color:var(--muted);font-weight:560}.brand .dot{color:var(--dim);margin:0 7px}
.ver{color:var(--dim);font-size:12px;font-variant-numeric:tabular-nums;letter-spacing:.02em}
.hero{position:relative;margin:30px 0;padding:38px 36px;border-radius:18px;
 background:linear-gradient(157deg,#171e2b 0%,#11161f 55%,#0d1119 100%);
 border:1px solid var(--line2);box-shadow:var(--elev);overflow:hidden}
.hero::before{content:"";position:absolute;inset:0 0 auto 0;height:2px;
 background:linear-gradient(90deg,var(--good2),rgba(86,211,100,0) 70%)}
.hero.warn::before{background:linear-gradient(90deg,var(--warn),rgba(214,165,51,0) 70%)}
.hero::after{content:"";position:absolute;right:-130px;top:-130px;width:380px;height:380px;border-radius:50%;
 background:radial-gradient(circle,rgba(86,211,100,.10),transparent 60%);pointer-events:none}
.hero.warn::after{background:radial-gradient(circle,rgba(214,165,51,.10),transparent 60%)}
.eyebrow{text-transform:uppercase;letter-spacing:.16em;font-size:11px;color:var(--muted);font-weight:640;margin-bottom:16px}
.headline{font-size:31px;line-height:1.16;font-weight:730;letter-spacing:-.022em;margin:0 0 18px;max-width:22ch}
.lede{color:var(--muted);font-size:15px;max-width:62ch;margin:0 0 26px}.lede b{color:var(--text);font-weight:620}
.verdict{display:inline-flex;align-items:flex-start;gap:11px;padding:13px 17px;border-radius:11px;
 background:rgba(86,211,100,.06);border:1px solid rgba(86,211,100,.24);font-size:14px;margin-bottom:28px;line-height:1.5}
.verdict.warn{background:rgba(214,165,51,.07);border-color:rgba(214,165,51,.3)}
.verdict .m{color:var(--good2);font-weight:800;font-size:15px}.verdict.warn .m{color:var(--warn)}
.hero-body{display:grid;grid-template-columns:1.55fr .95fr;gap:34px;align-items:center}
.hero-left .verdict{margin-bottom:0}
.hero-right{display:flex;flex-direction:column;gap:12px}
@media(max-width:720px){.hero-body{grid-template-columns:1fr;gap:24px}.headline{font-size:26px}}
.pill{background:rgba(255,255,255,.022);border:1px solid var(--line);border-radius:13px;padding:15px 17px;box-shadow:var(--elev)}
.pill .pv{font-size:27px;font-weight:720;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1}
.pv.g{color:var(--good2)}.pv.a{color:var(--accent)}.pv.d{color:var(--dim)}
.pill .pl{color:var(--text);font-size:13px;font-weight:580;margin-top:9px}
.pill .ps{color:var(--muted);font-size:11.5px;margin-top:8px;padding-top:8px;border-top:1px solid var(--line)}
.sec{margin:34px 0}
.sec .q{text-transform:uppercase;letter-spacing:.11em;font-size:11px;color:var(--accent);font-weight:660;margin-bottom:8px}
.sec h2{font-size:18px;font-weight:660;letter-spacing:-.01em;margin:0 0 14px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:13px;box-shadow:var(--elev);overflow:hidden}
.panel.pad{padding:18px 20px}
table{width:100%;border-collapse:collapse;font-size:14px}
thead th{text-align:left;color:var(--muted);font-weight:580;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
 padding:13px 20px;background:rgba(255,255,255,.014);border-bottom:1px solid var(--line)}
tbody td{padding:13px 20px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
tbody tr:last-child td{border-bottom:none}
th.n,td.n{text-align:right}
.g{color:var(--good2)}.w{color:var(--warn)}.dimv{color:var(--dim)}
.raw{color:var(--dim);font-size:10.5px;margin-top:3px;font-variant-numeric:tabular-nums;letter-spacing:.01em}
.statline{font-size:15px}.statline b{font-size:21px;font-weight:720;letter-spacing:-.01em}
.badge{display:inline-flex;align-items:center;gap:9px;font-size:14px;font-weight:540;padding:11px 15px;border-radius:9px;
 background:rgba(86,211,100,.07);border:1px solid rgba(86,211,100,.22)}
.badge.bad{background:rgba(248,81,73,.08);border-color:rgba(248,81,73,.28)}
.badge .m{color:var(--good2);font-weight:800}.badge.bad .m{color:var(--bad)}
.cap{color:var(--dim);font-size:12.5px;margin:13px 2px 0;line-height:1.6}
.foot{color:var(--dim);font-size:12px;margin-top:42px;padding-top:20px;border-top:1px solid var(--line);line-height:1.6}
"""


# Plain-English names for a decision-maker who doesn't know prusik internals — the
# technical id is kept as a muted second line for the engineer in the room.
_GATE_LABEL = {
    "evidence_gate": "Proof-it-actually-ran check",
    "critic": "Independent reviewer",
    "writable_gate": "Stay-in-scope guard",
    "phase_gate": "Required-steps gate",
    "rewind": "Back-up &amp; redo",
    "absence_detector": "Missing-deliverable check",
    "narrative_detector": "Unproven-claim check",
    "delta_detector": "Tests-stopped-running check",
}


def _overall_precision(ver: dict[str, Any]) -> str:
    tt = sum(v[catch_quality.TRUE_CATCH] for v in ver.values())
    res = sum(v[catch_quality.TRUE_CATCH] + v[catch_quality.FALSE_BLOCK]
              for v in ver.values())
    return f"{round(100 * tt / res)}%" if res else "–"


def _gate_rows(d: dict[str, Any], mode: str) -> str:
    """Panel table rows. mode='prec' → precision col; mode='enforced' → enforced col."""
    if not d:
        return "<tr><td class=dimv colspan=3>– no fires yet on this repo</td></tr>"
    out = []
    for g in sorted(d, key=lambda g: -d[g]["fired"]):
        s = d[g]
        if mode == "prec":
            p = s["precision"]
            cls = "g" if (p or 0) >= 0.9 else ("w" if p is not None and p < 0.5 else "")
            extra = f'<td class="n {cls}">{_pct(p)}</td>'
            mid = s["fired"]
        else:
            tc = s[catch_quality.TRUE_CATCH]
            extra = f'<td class="n g">{tc}</td>' if tc else '<td class="n dimv">–</td>'
            mid = s["fired"]
        label = _GATE_LABEL.get(g, html.escape(g))
        out.append(f'<tr><td>{label}<div class=raw>{html.escape(g)}</div></td>'
                   f"<td class=n>{mid}</td>{extra}</tr>")
    return "".join(out)


def render_html(rep: dict[str, Any]) -> str:
    from prusik import __version__
    f = rep["fidelity"]
    rec = rep["recall"]
    th = rep["throughput"]
    ver = rep["verification"]

    fid_ok = (not f["misses"] and not f["false_blocks"]) if f else None
    warn = rep["verdict"].startswith("⚠")
    hero_cls = "warn" if (warn or fid_ok is False) else ""

    # — hero copy (state-aware; aspirational but never beyond what the ledger shows) —
    if f is None:
        eyebrow, headline = ("Probe this repo to see the proof",
                             "Trust, one command from proven.")
    elif not fid_ok:
        eyebrow, headline = ("Resolve before you rely on the output",
                             "Gaps found — close these before trusting autonomous delivery.")
    else:
        eyebrow, headline = ("Proven on your codebase — not asserted",
                             "Autonomous delivery you can actually trust.")
    vmark = "⚠" if warn else "✓"
    verdict_txt = rep["verdict"].lstrip("✓⚠ ").strip()

    # — scorecard pills —
    if f is None:
        fid_pv, fid_cls, fid_sub = "–", "d", "run <code>prusik&nbsp;init</code> first"
    else:
        c, t = f["caught"]
        dc, dt = f["discrimination"]
        fid_pv = f"{c}/{t}"
        fid_cls = "g" if fid_ok else "d"
        fid_sub = f"and left {dc} of {dt} clean cases alone"
    prec = _overall_precision(ver)
    rp = "–" if rec["recall_pct"] is None else f"{rec['recall_pct']}%"
    rec_sub = (f"{rec['pending_candidates']} item(s) still under review"
               if rec["is_upper_bound"] else "nothing outstanding")

    bound = (f' <span class=dimv>· best case — {rec["pending_candidates"]} '
             f'item(s) still under review</span>' if rec["is_upper_bound"] else "")
    det_block = (f'<div class="panel" style="margin-top:14px">'
                 f'<table><thead><tr><th>extra check</th>'
                 f'<th class=n>times raised</th><th class=n>accuracy</th></tr></thead>'
                 f'<tbody>{_gate_rows(rec["detectors"], "prec")}</tbody></table></div>'
                 if rec["detectors"] else
                 '<p class=cap>Extra checks for problems that don\'t show up in the code '
                 'changes themselves are active — none has needed to flag anything '
                 'here yet.</p>')

    if f is None:
        fid_badge = ('<div class="badge bad"><span class=m>–</span> Not set up yet — '
                     'run <code>prusik init</code>, then re-run this report</div>')
    else:
        c, t = f["caught"]
        dc, dt = f["discrimination"]
        fid_badge = (f'<div class="badge {"" if fid_ok else "bad"}">'
                     f'<span class=m>{"✓" if fid_ok else "✗"}</span> '
                     f'Caught all {c} of {t} planted defects · correctly left '
                     f'{dc} of {dt} clean cases alone</div>')

    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>prusik · trust report</title><style>{_CSS}</style></head><body><div class=page>

<header class=topbar>
  <div class=brand>prusik<span class=dot>·</span><span class=s>trust report</span></div>
  <div class=ver>prusik {html.escape(__version__)}</div>
</header>

<section class="hero {hero_cls}">
 <div class=hero-body>
  <div class=hero-left>
   <div class=eyebrow>{eyebrow}</div>
   <h1 class=headline>{headline}</h1>
   <p class=lede>Every figure here comes from a tamper-evident record of what actually
   happened — <b>not the AI's own say-so</b>. That's the one assurance a self-reporting
   system can't give you: proof of work, measured on <b>your</b> code.</p>
   <div class="verdict {"warn" if warn else ""}"><span class=m>{vmark}</span>
   <span>{html.escape(verdict_txt)}</span></div>
  </div>
  <div class=hero-right>
    <div class=pill><div class="pv {fid_cls}">{fid_pv}</div>
      <div class=pl>Planted defects it caught</div><div class=ps>{fid_sub}</div></div>
    <div class=pill><div class="pv {"g" if prec.endswith("%") else "d"}">{prec}</div>
      <div class=pl>Alerts that were real problems</div>
      <div class=ps>when it flags something, how often it's right</div></div>
    <div class=pill><div class="pv {"a" if rp.endswith("%") else "d"}">{rp}</div>
      <div class=pl>Coverage, measured honestly</div><div class=ps>{rec_sub}</div></div>
  </div>
 </div>
</section>

<section class=sec>
  <div class=q>Does it catch real defects on your code?</div>
  <h2>Proven on your code</h2>
  <div class="panel pad">{fid_badge}
  <p class=cap>We deliberately plant the three mistakes an AI coder most often makes —
  working outside the agreed plan, pushing before it's ready, and claiming "done"
  without actually running the tests — and confirm the safeguards catch every one, while
  leaving correct work untouched. The system that checks your AI is itself checked here,
  on your code, on demand.</p></div>
</section>

<section class=sec>
  <div class=q>When it flags something — is it right?</div>
  <h2>When it raises an alert, it's a real problem</h2>
  <div class=panel><table>
  <thead><tr><th>safety check</th><th class=n>times raised</th>
  <th class=n>accuracy</th></tr></thead>
  <tbody>{_gate_rows(ver, "prec")}</tbody></table></div>
  <p class=cap>Accuracy = of the alerts it raised, the share that turned out to be real
  problems — confirmed from the record after the fact, never just claimed. These checks
  read the AI's own evidence and independently challenge its work, so a wrong "it
  passed" can't slip through.</p>
</section>

<section class=sec>
  <div class=q>And what does it miss?</div>
  <h2>What it might miss — measured, not hidden</h2>
  <div class="panel pad"><div class=statline><b>{rec["catches"]}</b> real problems caught ·
  <b>{rec["confirmed_misses"]}</b> confirmed misses · coverage <b>{rp}</b>{bound}</div>
  {det_block}
  <p class=cap>When a check passes there's nothing to record — so no honest tool can
  claim it never misses anything. Instead, every problem that slips past one check and
  is caught by a later one is counted here, against coverage. The figure you see is the
  <b>best case</b>; items still under review can only lower it, never raise it. Most
  tools advertise what they catch — this one also measures what it doesn't.</p></div>
</section>

<section class=sec>
  <div class=q>What does it stop before it ships?</div>
  <h2>Stopped before it could ship</h2>
  <div class=panel><table>
  <thead><tr><th>safeguard</th><th class=n>times it stepped in</th>
  <th class=n>required &amp; fixed</th></tr></thead>
  <tbody>{_gate_rows(rep["prevention"], "enforced")}</tbody></table></div>
  <p class=cap>These are counts, not accuracy scores — a safeguard's value is the mistake
  it prevents, which by its nature never becomes a visible problem. <b>Required &amp;
  fixed</b> means it held the work back until a missing requirement was actually
  produced, then let it proceed.</p>
</section>

<section class=sec>
  <div class=q>How much has it delivered — on its own?</div>
  <h2>Work delivered</h2>
  <div class="panel pad"><div class=statline><b>{th["sprints_completed"]}</b> features
  delivered end-to-end <span class=dimv>/ {th["sprints_started"]} started</span></div></div>
</section>

<p class=foot>Every number here comes from this repository's own activity record; "–"
means there isn't evidence for it yet — nothing is claimed without proof. Generated
offline by <code>prusik trust-report</code>, from the record on your machine.</p>
</div></body></html>"""


def run(json_output: bool = False, html_out: str | None = None) -> int:
    from prusik import phases
    records = ledger.read_all()
    config = phases.load_sprint_config()
    rep = compose(records, config)

    if html_out:
        try:
            Path(html_out).write_text(render_html(rep))
            print(f"[prusik-trust] wrote {html_out}")
        except OSError as e:
            print(f"[prusik-trust] could not write {html_out}: {e}")
            return 1

    if json_output:
        print(json.dumps(rep, indent=2))
    elif not html_out:
        print(render_text(rep))
    return 0
