import { fmt, loop } from "@/lib/data";

export const dynamic = "force-dynamic";

function Bars({ label, values, max }) {
  return (
    <tr>
      <td>{label}</td>
      <td style={{ width: "60%" }}>
        {values.map((v, i) => (
          <div key={i} className="bar" style={{ margin: "4px 0" }} title={fmt.ms(v)}><span style={{ width: `${Math.min(100, (100 * v) / max)}%` }} /></div>
        ))}
      </td>
      <td className="num">{values.map((v) => fmt.ms(v)).join(" · ")}</td>
    </tr>
  );
}

export default async function LoopPage({ params }) {
  const { id } = await params;
  const r = loop(id);
  if (!r) return <p>Loop {id} not found.</p>;
  const v = r.verification;
  const max = Math.max(...(v?.before_ms ?? [1]), ...(v?.after_ms ?? [1]));
  return (
    <>
      <a href="/" className="small">← overview</a>
      <h1>Closed loop: {r.route}</h1>
      <div className="muted">{r.loop_id} · baseline {r.baseline_campaign} · {r.repeats} runs before and after</div>
      <h2>What the system did</h2>
      <ol className="steps">
        <li><b>1 · Observe</b>{r.operation} ran with a fault the system was not told about (revealed after the loop: <code>{r.injected_variant}</code>).</li>
        <li><b>2 · Detect</b><span className={r.detection.anomalous ? "bad" : "good"}>{r.detection.anomalous ? "ANOMALOUS" : "normal"}</span> · {r.detection.verdicts[0]?.reason}</li>
        {r.diagnosis ? (
          <li><b>3 · Diagnose</b><b>{fmt.cls(r.diagnosis.class)}</b> ({r.diagnosis.confidence})
            <ul className="evidence">{r.diagnosis.evidence.map((e) => <li key={e}>{e}</li>)}</ul></li>
        ) : null}
        {r.recommendation ? (<li><b>4 · Remediate</b>{r.recommendation.recommendation}: {r.recommendation.action}. Applied variant <code>{v?.fix_variant ?? "–"}</code>.</li>) : null}
        {v ? (
          <li><b>5 · Verify</b>
            server p50 ×{v.improvement.ratio.toFixed(3)} (p {v.improvement.p_one_sided.toFixed(4)}), healthy {fmt.ms(v.healthy_median_ms)}, vs healthy {fmt.ratio(v.back_to_healthy.ratio)}{" "}
            → <span className={`tag ${r.outcome.startsWith("improvement verified") ? "good" : "warn"}`}>{r.outcome}</span>
          </li>
        ) : <li><b>Outcome</b>{r.outcome}</li>}
      </ol>
      {v ? (
        <>
          <h2>Server-side latency per run</h2>
          <div className="panel">
            <table><tbody>
              <Bars label="Before (faulty)" values={v.before_ms} max={max} />
              <Bars label="After (fixed)" values={v.after_ms} max={max} />
            </tbody></table>
          </div>
        </>
      ) : null}
      <div className="source">Source: data/results/loops/{r.loop_id}/report.json</div>
    </>
  );
}
