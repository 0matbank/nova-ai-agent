import { useCallback, useEffect, useState } from "react";
import { api, takeToken } from "./api.js";

const TABS = [
  ["overview", "Overview"],
  ["tasks", "Tasks"],
  ["approvals", "Approvals"],
  ["providers", "Providers & Models"],
  ["agents", "Agents"],
  ["skills", "Skills"],
  ["memory", "Memory"],
  ["schedules", "Schedules"],
  ["logs", "Logs"],
  ["settings", "Settings"],
];

const STATE_CLASS = {
  COMPLETED: "ok", FAILED: "bad", CANCELLED: "muted", RUNNING: "run", PLANNING: "run",
  VERIFYING: "run", RETRYING: "warn", WAITING_APPROVAL: "warn", WAITING_DESKTOP: "warn",
  WAITING_RESOURCE: "warn", PAUSED: "warn", RECEIVED: "run",
};

function usePoll(path, ms = 5000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api(path));
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, [path]);
  useEffect(() => {
    load();
    if (!ms) return undefined;
    const t = setInterval(load, ms);
    return () => clearInterval(t);
  }, [load, ms]);
  return [data, error, load];
}

function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString();
}

function Badge({ state }) {
  return <span className={`badge ${STATE_CLASS[state] || ""}`}>{state}</span>;
}

function ErrorLine({ error }) {
  if (!error) return null;
  return <p className="error">⚠️ {error.status === 401 ? "Token মেলেনি — scripts/dashboard.py দিয়ে আবার খুলুন" : error.message}</p>;
}

// ------------------------------------------------------------------ views

function Overview({ go }) {
  const [s, err, reload] = usePoll("status", 5000);
  const [active] = usePoll("tasks?scope=active", 5000);
  const [appr, , reloadAppr] = usePoll("approvals", 5000);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");

  async function submit(e) {
    e.preventDefault();
    if (!text.trim()) return;
    try {
      const t = await api("tasks", { method: "POST", body: { text } });
      setMsg(`📥 Task #${t.id} তৈরি হয়েছে — ফলাফল Telegram-এও যাবে`);
      setText("");
    } catch (e2) {
      setMsg(`⚠️ ${e2.message}`);
    }
  }
  async function queue(op) {
    await api(`queue/${op}`, { method: "POST" });
    reload();
  }
  async function lockdown(on) {
    if (on && !confirm("LOCKDOWN চালু করবেন? সব dangerous কাজ বন্ধ হবে।")) return;
    const r = await api("lockdown", { method: "POST", body: { on } });
    setMsg(r.message);
    reload();
  }
  if (!s) return <><ErrorLine error={err} /><p>লোড হচ্ছে…</p></>;
  const pc = s.pc;
  return (
    <div className="grid">
      <section className="card wide">
        <h2>{s.agent} — {s.health === "OK" ? "✅ OK" : "⚠️ DEGRADED"}</h2>
        <p className="muted">version {s.version} · uptime {Math.floor(s.uptime_seconds / 60)} min
          {s.running_task ? ` · এখন চলছে #${s.running_task}` : ""}
          {s.queue_paused ? " · ⏸️ queue paused" : ""}{s.lockdown ? " · 🚨 LOCKDOWN" : ""}</p>
        <div className="row">
          {s.queue_paused
            ? <button onClick={() => queue("resume")}>▶️ Queue resume</button>
            : <button onClick={() => queue("pause")}>⏸️ Queue pause</button>}
          {s.lockdown
            ? <button onClick={() => lockdown(false)}>🔓 Lockdown off</button>
            : <button className="danger" onClick={() => lockdown(true)}>🚨 Lockdown</button>}
        </div>
        <form onSubmit={submit} className="row">
          <input value={text} onChange={(e) => setText(e.target.value)}
                 placeholder="Nova-কে কাজ দিন (Telegram-এর মতোই) …" />
          <button type="submit">পাঠাও</button>
        </form>
        {msg && <p className="note">{msg}</p>}
      </section>

      <section className="card">
        <h3>Health</h3>
        <ul className="plain">
          {s.probes.map((p) => (
            <li key={p.name}>{p.ok ? "✅" : "❌"} <b>{p.name}</b>: {p.detail}</li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h3>PC resources</h3>
        <Meter label="CPU" pct={pc.cpu_percent} />
        <Meter label={`RAM ${pc.ram_used_gb.toFixed(1)}/${pc.ram_total_gb.toFixed(1)} GB`}
               pct={pc.ram_percent} />
        {pc.gpus.map((g, i) => (
          <Meter key={i} label={`GPU ${g.name} · VRAM ${g.vram_used_gb?.toFixed?.(1) ?? "?"}/${g.vram_total_gb?.toFixed?.(1) ?? "?"} GB`}
                 pct={g.util_percent ?? 0} />
        ))}
        {s.drives.map((d) => (
          <Meter key={d.drive} label={`${d.drive} ${d.free_gb} GB free of ${d.total_gb}`}
                 pct={d.used_percent} />
        ))}
      </section>

      <section className="card">
        <h3>Active tasks</h3>
        {!active?.length && <p className="muted">কিছু চলছে না</p>}
        <ul className="plain">
          {active?.map((t) => (
            <li key={t.id}><a onClick={() => go("tasks", t.id)}>#{t.id}</a> <Badge state={t.state} /> {t.title}</li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h3>Pending approvals</h3>
        <PendingApprovals appr={appr} reload={reloadAppr} />
      </section>
    </div>
  );
}

function Meter({ label, pct }) {
  const v = Math.max(0, Math.min(100, Number(pct) || 0));
  const cls = v >= 90 ? "bad" : v >= 75 ? "warn" : "ok";
  return (
    <div className="meter">
      <div className="meter-label">{label} <span>{v.toFixed(0)}%</span></div>
      <div className="meter-bar"><div className={`meter-fill ${cls}`} style={{ width: `${v}%` }} /></div>
    </div>
  );
}

function PendingApprovals({ appr, reload }) {
  const [busy, setBusy] = useState(null);
  async function decide(id, approve) {
    setBusy(id);
    try {
      await api(`approvals/${id}`, { method: "POST", body: { approve } });
    } finally {
      setBusy(null);
      reload();
    }
  }
  if (!appr) return <p className="muted">…</p>;
  if (!appr.pending.length) return <p className="muted">কোনো approval অপেক্ষায় নেই</p>;
  return (
    <ul className="plain">
      {appr.pending.map((a) => (
        <li key={a.id} className="approval">
          <div><span className={`badge ${a.level === "RED" ? "bad" : "warn"}`}>{a.level}</span> <b>{a.action}</b> — task #{a.task_id}</div>
          <div className="muted">{a.target}</div>
          <pre>{a.summary}</pre>
          <div className="row">
            <button disabled={busy === a.id} onClick={() => decide(a.id, true)}>✅ Approve</button>
            <button disabled={busy === a.id} className="danger" onClick={() => decide(a.id, false)}>❌ Reject</button>
          </div>
        </li>
      ))}
    </ul>
  );
}

function Tasks({ focus }) {
  const [rows, err] = usePoll("tasks?limit=60", 5000);
  const [sel, setSel] = useState(focus || null);
  const [detail, , reload] = usePoll(sel ? `tasks/${sel}` : null, 4000);
  async function cancel(id) {
    if (!confirm(`Task #${id} বাতিল করবেন?`)) return;
    await api(`tasks/${id}/cancel`, { method: "POST" });
    reload();
  }
  return (
    <div className="split">
      <section className="card">
        <h3>Task history</h3>
        <ErrorLine error={err} />
        <table>
          <thead><tr><th>#</th><th>State</th><th>Title</th><th>Created</th></tr></thead>
          <tbody>
            {rows?.map((t) => (
              <tr key={t.id} className={sel === t.id ? "sel" : ""} onClick={() => setSel(t.id)}>
                <td>{t.id}</td><td><Badge state={t.state} /></td><td>{t.title}</td><td>{when(t.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        {!detail && <p className="muted">একটা task বেছে নিন</p>}
        {detail && (
          <>
            <h3>Task #{detail.id} <Badge state={detail.state} /></h3>
            <p><b>Request:</b> {detail.request_text}</p>
            <p className="muted">{detail.task_type} · via {detail.channel} · retries {detail.retry_count}</p>
            {detail.result_summary && <pre>{detail.result_summary}</pre>}
            {detail.error_message && <pre className="error">{detail.error_code}: {detail.error_message}</pre>}
            <h4>Steps</h4>
            <ol>
              {detail.steps.map((s) => (
                <li key={s.seq}>{s.title} — <i>{s.status}</i>{s.error ? ` (${s.error})` : ""}</li>
              ))}
            </ol>
            {!["COMPLETED", "FAILED", "CANCELLED"].includes(detail.state) &&
              <button className="danger" onClick={() => cancel(detail.id)}>🛑 Cancel</button>}
          </>
        )}
      </section>
    </div>
  );
}

function Approvals() {
  const [appr, err, reload] = usePoll("approvals", 4000);
  return (
    <div className="split">
      <section className="card"><h3>Pending</h3><ErrorLine error={err} /><PendingApprovals appr={appr} reload={reload} /></section>
      <section className="card">
        <h3>History</h3>
        <table>
          <thead><tr><th>#</th><th>Action</th><th>Status</th><th>By</th><th>Decided</th></tr></thead>
          <tbody>
            {appr?.recent.map((a) => (
              <tr key={a.id} title={a.summary}>
                <td>{a.id}</td><td>{a.action}<div className="muted">{a.target}</div></td>
                <td>{a.status}</td><td>{a.decided_by || "—"}</td><td>{when(a.decided_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function Providers() {
  const [p, err] = usePoll("providers", 10000);
  const [m] = usePoll("models", 15000);
  return (
    <div className="split">
      <section className="card">
        <h3>AI providers</h3><ErrorLine error={err} />
        <table>
          <thead><tr><th>Provider</th><th>State</th><th>Routable</th><th>Circuit</th></tr></thead>
          <tbody>
            {p?.map((x) => (
              <tr key={x.name} title={x.detail}>
                <td>{x.name}</td><td>{x.state}</td><td>{x.routable ? "yes" : "no"}</td>
                <td>{x.circuit_open_seconds ? `open ${Math.ceil(x.circuit_open_seconds / 60)}m — ${x.circuit_reason}` : "closed"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h3>Models</h3>
        <h4>Loaded in Ollama now</h4>
        <ul className="plain">
          {m?.loaded?.map((x, i) => (
            <li key={i}>{x.error ? `Ollama: ${x.error}` : `${x.name} — ${x.size_gb} GB (VRAM ${x.vram_gb} GB)`}</li>
          ))}
          {m && !m.loaded?.length && <li className="muted">কোনো model লোড নেই</li>}
        </ul>
        <h4>Aliases</h4>
        <pre>{m ? JSON.stringify(m.alias_sets, null, 2) : "…"}</pre>
      </section>
    </div>
  );
}

function Agents() {
  const [rows, err] = usePoll("agents", 0);
  return (
    <section className="card">
      <h3>Agents</h3><ErrorLine error={err} />
      <table>
        <thead><tr><th>Agent</th><th>Task types</th><th>AI work</th><th>Skills</th></tr></thead>
        <tbody>
          {rows?.map((a) => (
            <tr key={a.name}><td><b>{a.title}</b><div className="muted">{a.name}{a.enabled ? "" : " (off)"}</div></td>
              <td>{a.task_types.join(", ")}</td><td>{a.provider_task_type || "—"}</td><td>{a.skills.join(", ") || "—"}</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Skills() {
  const [rows, err] = usePoll("skills", 0);
  return (
    <section className="card">
      <h3>Skills & permission levels</h3><ErrorLine error={err} />
      {rows?.map((s) => (
        <details key={s.name}>
          <summary><b>{s.name}</b> — {s.description}</summary>
          <ul className="plain">
            {s.tools.map((t) => (
              <li key={t.tool}><code>{s.name}.{t.tool}</code>{" "}
                {t.actions.map((a) => <span key={a.action} className={`badge lvl-${a.level}`}>{a.action}: {a.level}</span>)}
              </li>
            ))}
          </ul>
        </details>
      ))}
    </section>
  );
}

function Memory() {
  const [data, err] = usePoll("memory", 0);
  return (
    <section className="card">
      <h3>Project memory</h3><ErrorLine error={err} />
      {data && Object.entries(data.projects).map(([key, name]) => (
        <details key={key} open>
          <summary><b>{name}</b> ({data.entries[key]?.length || 0})</summary>
          <ul className="plain">
            {data.entries[key]?.map((e) => (
              <li key={e.id}><span className="badge">{e.kind}</span> {e.content} <span className="muted">#{e.id} · {when(e.updated_at)}</span></li>
            ))}
          </ul>
        </details>
      ))}
    </section>
  );
}

function Schedules() {
  const [rows, err, reload] = usePoll("schedules", 10000);
  async function cancel(id) {
    if (!confirm(`Schedule #${id} বাতিল করবেন?`)) return;
    await api(`schedules/${id}/cancel`, { method: "POST" });
    reload();
  }
  return (
    <section className="card">
      <h3>Schedules</h3><ErrorLine error={err} />
      {!rows?.length && <p className="muted">কোনো schedule নেই</p>}
      <table>
        <tbody>
          {rows?.map((s) => (
            <tr key={s.id}><td>#{s.id}</td><td>{s.kind}</td><td>{s.what}</td>
              <td>next: {when(s.next_run_at)}</td><td>runs {s.run_count}</td>
              <td><button className="danger" onClick={() => cancel(s.id)}>Cancel</button></td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Logs() {
  const [rows, err] = usePoll("logs?limit=150", 8000);
  return (
    <section className="card">
      <h3>Audit log</h3><ErrorLine error={err} />
      <table>
        <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Task</th><th>Status</th></tr></thead>
        <tbody>
          {rows?.map((r) => (
            <tr key={r.id}><td>{when(r.ts)}</td><td>{r.actor}</td><td>{r.action}</td>
              <td className="wrap">{r.target}</td><td>{r.task_id ?? ""}</td><td>{r.status}</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Settings() {
  const [s, err] = usePoll("settings", 0);
  return (
    <section className="card">
      <h3>Settings (read-only)</h3><ErrorLine error={err} />
      {s && <p className="note">{s.note}</p>}
      <pre>{s ? JSON.stringify(s, null, 2) : "…"}</pre>
    </section>
  );
}

// ------------------------------------------------------------------ shell

export default function App() {
  const [token] = useState(() => takeToken());
  const [tab, setTab] = useState("overview");
  const [focus, setFocus] = useState(null);
  const go = (t, id) => { setTab(t); setFocus(id ?? null); };
  if (!token) {
    return (
      <main className="center">
        <h1>Nova AI Dashboard</h1>
        <p>এই পাতা খুলতে PC-তে চালান: <code>uv run python scripts/dashboard.py</code></p>
      </main>
    );
  }
  const View = { overview: Overview, tasks: Tasks, approvals: Approvals, providers: Providers,
    agents: Agents, skills: Skills, memory: Memory, schedules: Schedules, logs: Logs,
    settings: Settings }[tab];
  return (
    <div className="app">
      <nav>
        <div className="brand">🤖 Nova AI</div>
        {TABS.map(([key, label]) => (
          <button key={key} className={tab === key ? "active" : ""} onClick={() => go(key)}>{label}</button>
        ))}
        <div className="foot">127.0.0.1 only · same approvals as Telegram</div>
      </nav>
      <main>
        <View go={go} focus={focus} key={`${tab}-${focus}`} />
      </main>
    </div>
  );
}
