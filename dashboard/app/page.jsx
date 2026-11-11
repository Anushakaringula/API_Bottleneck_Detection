import { campaign, campaigns, fmt, loops } from "@/lib/data";

export const dynamic = "force-dynamic";

function Card({ label, value, sub }) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub ? <div className="sub">{sub}</div> : null}
    </div>
  );
}

function endpointRows(c) {
  const rows = {};
  for (const run of c.runs) {
    const route = c.routeByOperation[run.operation] ?? run.operation;
    const row = (rows[route] ??= { route, operation: run.operation, faults: 0, detected: 0, diagnosed: 0, conditions: new Set() });
    if (run.fault_class && run.fault_class !== "NONE") {
      row.faults += 1;
      row.conditions.add(run.variant);
      if (c.verdictById[run.experiment_id]?.verdict === "anomalous") row.detected += 1;
      const d = c.diagnosisById[run.experiment_id];
      if (d && d.diagnosis === d.truth) row.diagnosed += 1;
    }
  }
  const healthy = Object.fromEntries((c.summary?.healthy_noise ?? []).map((n) => [n.operation, n.server_median_p50_ms]));
  return Object.values(rows).map((r) => ({ ...r, healthy: healthy[r.operation] })).sort((a, b) => a.route.localeCompare(b.route));
}

export default async function Overview({ searchParams }) {
  const params = await searchParams;
  const ids = campaigns();
  if (!ids.length) {
    return <p>No results yet. Run a campaign, then <code>features.extract</code>, <code>evaluation.detection</code>, <code>evaluation.diagnosis</code> and <code>evaluation.verification</code>.</p>;
  }
  const id = ids.includes(params?.campaign) ? params.campaign : ids[0];
  const c = campaign(id);
  const det = c.detection;
  const fa = det?.false_alarms_on_healthy;
  const dr = det?.detection_on_faults;
  const rules = c.diagnosis?.rules?.metrics;
  const ml = c.diagnosis?.ml ?? {};
  const scheme = c.diagnosis && c.diagnosis.train_campaign_id !== c.diagnosis.campaign_id ? "final" : "grouped";
  const best = Object.entries(ml).filter(([k]) => k.startsWith("L4/")).map(([k, v]) => [k.split("/")[1], v?.[scheme]?.["abstain_0.0"]?.macro_f1]).sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0))[0];
  const pairs = c.verification?.pairs ?? [];
  const endpoints = endpointRows(c);
  const loopReports = loops();
  const tel = c.summary?.telemetry;

  return (
    <>
      <h1>{id}</h1>
      <div className="muted">
        {c.summary ? `${c.summary.analysed_runs ?? c.summary.completed_runs} runs analysed of ${c.summary.planned_runs} planned` : ""}
        {c.summary?.exclusions?.excluded_runs?.length ? ` · ${c.summary.exclusions.excluded_runs.length} excluded by the protocol` : ""}
      </div>
      <div className="selector">
        {ids.map((x) => <a key={x} href={`/?campaign=${x}`} className={x === id ? "active" : ""}>{x}</a>)}
      </div>

      <div className="cards">
        <Card label="Requests matched to spans" value={fmt.pct(tel?.request_match_rate, 2)} sub={tel ? `${tel.requests_matched_to_one_span} of ${tel.requests_attempted_incl_warmup}` : ""} />
        <Card label="False alarms (healthy runs)" value={fmt.pct(fa?.rate)} sub={fa ? `${fa.anomalous}/${fa.decided} · 95 % CI ${fmt.ci(fa.ci95)}` : ""} />
        <Card label="Faults detected" value={fmt.pct(dr?.rate)} sub={dr ? `${dr.anomalous}/${dr.decided} · 95 % CI ${fmt.ci(dr.ci95)}` : ""} />
        <Card label="Diagnosis macro-F1 (rules)" value={fmt.num(rules?.macro_f1)} sub={best ? `best ML (L4, ${scheme}): ${best[0].replace("_", " ")} ${fmt.num(best[1])}` : ""} />
        <Card label="Fixes verified" value={pairs.length ? `${pairs.filter((p) => p.improved).length}/${pairs.length}` : "–"} sub={pairs.length ? `${pairs.filter((p) => p.verdict === "improved, back to healthy").length} back to healthy (±10 %)` : ""} />
      </div>

      <h2>Endpoints</h2>
      <div className="panel">
        <table>
          <thead><tr><th>Endpoint</th><th className="num">Healthy p50 (server)</th><th>Faults tested</th><th className="num">Detected</th><th className="num">Correctly diagnosed</th><th></th></tr></thead>
          <tbody>
            {endpoints.map((e) => (
              <tr key={e.route}>
                <td><b>{e.route}</b> <span className="muted small">{e.operation}</span></td>
                <td className="num">{fmt.ms(e.healthy)}</td>
                <td className="small">{[...e.conditions].sort().join(", ") || "–"}</td>
                <td className="num">{e.faults ? <span className={e.detected === e.faults ? "good" : "warn"}>{e.detected}/{e.faults}</span> : "–"}</td>
                <td className="num">{e.faults ? <span className={e.diagnosed === e.faults ? "good" : "warn"}>{e.diagnosed}/{e.faults}</span> : "–"}</td>
                <td><a href={`/endpoint?campaign=${id}&operation=${encodeURIComponent(e.operation)}`}>evidence →</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {det ? (
        <>
          <h2>Detection by fault class</h2>
          <div className="panel">
            <table>
              <thead><tr><th>Class</th><th className="num">Runs</th><th className="num">Detected</th><th className="num">Rate</th><th className="num">95 % CI</th></tr></thead>
              <tbody>
                {Object.entries(det.by_fault_class).map(([k, v]) => (
                  <tr key={k}><td>{fmt.cls(k)}</td><td className="num">{v.decided}</td><td className="num">{v.anomalous}</td><td className="num">{fmt.pct(v.rate)}</td><td className="num">{fmt.ci(v.ci95)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="source">Rule: server-side run p50 &gt; max(P99 of healthy runs, 1.2 × healthy median) per operation · data/results/detection/{id}/</div>
        </>
      ) : null}

      {c.diagnosis ? (
        <>
          <h2>Diagnosis: rules vs Random Forest vs XGBoost (macro-F1)</h2>
          <div className="panel">
            <table>
              <thead><tr><th>Method</th>{[1, 2, 3, 4].map((l) => <th key={l} className="num">L{l}</th>)}</tr></thead>
              <tbody>
                <tr><td>Rules (mechanism evidence)</td><td className="num">–</td><td className="num">–</td><td className="num">–</td><td className="num"><b>{fmt.num(rules?.macro_f1)}</b></td></tr>
                {["random_forest", "xgboost"].map((m) => (
                  <tr key={m}><td>{m === "xgboost" ? "XGBoost" : "Random Forest"}</td>
                    {[1, 2, 3, 4].map((l) => <td key={l} className="num">{fmt.num(ml[`L${l}/${m}`]?.[scheme]?.["abstain_0.0"]?.macro_f1)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="source">{c.diagnosis.fault_runs} fault runs · scheme “{scheme}” · L1 client → L2 server timing → L3 runtime → L4 database/cache tracing · data/results/diagnosis/{id}/</div>
        </>
      ) : null}

      {pairs.length ? (
        <>
          <h2>Remediation verified</h2>
          <div className="panel">
            <table>
              <thead><tr><th>Fix</th><th className="num">Faulty → fixed (healthy)</th><th className="num">Ratio</th><th className="num">p (Holm)</th><th className="num">vs healthy</th><th>Verdict</th></tr></thead>
              <tbody>
                {pairs.map((p) => (
                  <tr key={p.pair}>
                    <td>{p.pair}</td>
                    <td className="num">{fmt.ms(p.median_ms.faulty)} → {fmt.ms(p.median_ms.fixed)} ({fmt.ms(p.median_ms.healthy)})</td>
                    <td className="num">{fmt.ratio(p.improvement.ratio)}</td>
                    <td className="num">{fmt.num(p.improvement.p_holm, 4)}</td>
                    <td className="num">{fmt.ratio(p.back_to_healthy.ratio)}</td>
                    <td><span className={`tag ${p.verdict === "improved, back to healthy" ? "good" : p.improved ? "warn" : "bad"}`}>{p.verdict}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="source">
            Right fix recommended for {c.verification.remediation_mapping.recommendation_matches_fault}/{c.verification.remediation_mapping.fault_runs} fault runs · Welch on log latency, TOST ±10 % · data/results/verification/{id}/
          </div>
        </>
      ) : null}

      <h2>Closed-loop runs</h2>
      {loopReports.length ? (
        <div className="panel">
          <table>
            <thead><tr><th>Loop</th><th>Operation</th><th>Diagnosis</th><th>Fix applied</th><th>Outcome</th></tr></thead>
            <tbody>
              {loopReports.map((l) => (
                <tr key={l.loop_id}>
                  <td><a href={`/loops/${l.loop_id}`}>{l.loop_id}</a></td>
                  <td>{l.route}</td>
                  <td>{fmt.cls(l.diagnosis?.class)}</td>
                  <td>{l.verification?.fix_variant ?? "–"}</td>
                  <td><span className={`tag ${l.outcome?.startsWith("improvement verified") ? "good" : "warn"}`}>{l.outcome}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="muted">None yet: <code>python -m campaign.loop --operation users.list --variant nplus1:20 --baseline-campaign &lt;main-id&gt;</code></p>}
    </>
  );
}
