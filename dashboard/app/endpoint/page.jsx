import { campaign, fmt } from "@/lib/data";

export const dynamic = "force-dynamic";

function LatencyBar({ value, threshold, max }) {
  if (value == null || !max) return null;
  const width = Math.min(100, (100 * value) / max);
  return (
    <div className="bar" title={`${fmt.ms(value)} vs threshold ${fmt.ms(threshold)}`}>
      <span className={threshold != null && value > threshold ? "over" : ""} style={{ width: `${width}%` }} />
      {threshold != null ? <i style={{ left: `${Math.min(100, (100 * threshold) / max)}%` }} /> : null}
    </div>
  );
}

export default async function Endpoint({ searchParams }) {
  const { campaign: id, operation } = await searchParams;
  const c = campaign(id);
  const runs = c.runs.filter((r) => r.operation === operation);
  const route = c.routeByOperation[operation] ?? operation;
  const rows = runs.map((r) => ({ run: r, verdict: c.verdictById[r.experiment_id], diagnosis: c.diagnosisById[r.experiment_id] }))
    .sort((a, b) => a.run.condition_id.localeCompare(b.run.condition_id) || (a.run.repeat ?? 0) - (b.run.repeat ?? 0));
  const values = rows.map((x) => x.verdict?.value).filter((v) => v != null);
  const max = Math.max(...values, ...rows.map((x) => x.verdict?.threshold ?? 0));

  return (
    <>
      <a href={`/?campaign=${id}`} className="small">← {id}</a>
      <h1>{route}</h1>
      <div className="muted">
        Operation {operation} · {rows.length} runs · threshold {fmt.ms(rows.find((x) => x.verdict?.threshold != null)?.verdict.threshold)}
        {" "}(healthy median {fmt.ms(rows.find((x) => x.verdict?.baseline_median != null)?.verdict.baseline_median)})
      </div>
      <h2>Runs: detection, diagnosis and recommended fix</h2>
      <div className="panel">
        <table>
          <thead>
            <tr><th>Condition (injected)</th><th className="num">Server p50</th><th>vs threshold</th><th>Detection</th><th>Diagnosis</th><th>Evidence</th><th>Recommended fix</th></tr>
          </thead>
          <tbody>
            {rows.map(({ run, verdict, diagnosis }) => (
              <tr key={run.experiment_id}>
                <td className="small">{run.variant}{run.repeat ? ` #${run.repeat}` : ""}{run.excluded_reasons?.length ? <div className="warn">excluded</div> : null}</td>
                <td className="num">{fmt.ms(verdict?.value)}<div className="muted small">{fmt.ratio(verdict?.ratio_to_baseline)}</div></td>
                <td><LatencyBar value={verdict?.value} threshold={verdict?.threshold} max={max} /></td>
                <td><span className={`tag ${verdict?.verdict === "anomalous" ? (run.fault_class === "NONE" ? "bad" : "good") : verdict?.verdict === "normal" ? (run.fault_class === "NONE" ? "good" : "bad") : "warn"}`}>{verdict?.verdict ?? "–"}</span></td>
                <td>{diagnosis ? <span className={diagnosis.diagnosis === diagnosis.truth ? "good" : "bad"}>{fmt.cls(diagnosis.diagnosis)}</span> : <span className="muted">–</span>}</td>
                <td className="small">{diagnosis ? <ul className="evidence">{diagnosis.evidence.map((e) => <li key={e}>{e}</li>)}</ul> : null}</td>
                <td className="small">{diagnosis?.recommendation?.recommendation ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="source">
        Green = correct (fault detected / healthy run left alone / right diagnosis); red = wrong. Diagnosis is shown for fault runs.
        Sources: data/results/detection/{id}/verdicts.jsonl, data/results/diagnosis/{id}/summary.json, data/results/campaigns/{id}/summary.json
      </div>
    </>
  );
}
