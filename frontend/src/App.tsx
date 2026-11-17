import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  ChevronDown,
  BookOpen,
  ChevronRight,
  FileText,
  Layers3,
  LoaderCircle,
  LogOut,
  Plus,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Api, ApiError, message } from "./api";
import DocumentLibrary from "./DocumentLibrary";
import AnswerPanel from "./AnswerPanel";
import type { Answer, Document, Session, Status } from "./api";

const terminal = (status: Status) =>
  status === "completed" || status === "failed";
function Alert({ text }: { text: string }) {
  return (
    <div className="alert" role="alert">
      {text}
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null),
    [notice, setNotice] = useState("");
  // A session-keyed subtree discards all document/query/polling state on switch.
  function end(text = "") {
    setSession(null);
    setNotice(text);
  }
  return session ? (
    <Workspace key={session.access_token} session={session} end={end} />
  ) : (
    <Login
      notice={notice}
      onLogin={(s) => {
        setNotice("");
        setSession(s);
      }}
    />
  );
}
function Login({
  notice,
  onLogin,
}: {
  notice: string;
  onLogin: (session: Session) => void;
}) {
  const [account, setAccount] = useState("acme-demo"),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const c = new AbortController();
    controller.current = c;
    setBusy(true);
    setError("");
    try {
      const s = await new Api("", c.signal).login(account, password);
      if (!c.signal.aborted) {
        setPassword("");
        onLogin(s);
      }
    } catch (e) {
      if (!c.signal.aborted)
        setError(
          e instanceof ApiError && e.status === 401
            ? "Account or password is incorrect."
            : message(e),
        );
    } finally {
      if (!c.signal.aborted) {
        setPassword("");
        setBusy(false);
      }
    }
  }
  return (
    <main className="login">
      <section className="login-story">
        <a className="brand" href="/" aria-label="Atlas home">
          <Layers3 /> atlas<span> / enterprise rag</span>
        </a>
        <div>
          <div className="eyebrow">KNOWLEDGE, CONNECTED</div>
          <h1>
            Your documents.
            <br />A clearer perspective.
          </h1>
          <p>
            A private workspace for turning company knowledge into answers you
            can trace back to the source.
          </p>
          <div className="story-cards">
            <div>
              <ShieldCheck />
              <strong>Tenant by design</strong>
              <span>Separate workspaces. Verified access.</span>
            </div>
            <div>
              <BookOpen />
              <strong>Evidence in every answer</strong>
              <span>Follow citations to the original PDF.</span>
            </div>
          </div>
        </div>
        <span className="story-footer">
          ENTERPRISE RAG PLATFORM <span>LOCAL DEMO · 01</span>
        </span>
      </section>
      <section className="login-panel">
        <div className="login-box">
          <span className="pill">LOCAL WORKSPACE</span>
          <h2>Welcome to Atlas</h2>
          <p>Sign in to explore your organization’s knowledge.</p>
          {notice && <Alert text={notice} />}
          <form onSubmit={submit}>
            <label>
              Demo account
              <select
                value={account}
                onChange={(e) => setAccount(e.target.value)}
                disabled={busy}
              >
                <option value="acme-demo">ACME · acme-demo</option>
                <option value="globex-demo">GLOBEX · globex-demo</option>
              </select>
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete="current-password"
                required
                maxLength={256}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={busy}
              />
            </label>
            {error && <Alert text={error} />}
            <button className="primary" disabled={busy}>
              {busy ? (
                <LoaderCircle className="spin" />
              ) : (
                <>
                  Enter workspace <ArrowRight size={17} />
                </>
              )}
            </button>
          </form>
          <p className="fine">
            <ShieldCheck size={15} /> Credentials are configured by your demo
            administrator. Sessions stay in memory and expire after 15 minutes.
          </p>
        </div>
      </section>
    </main>
  );
}
function Workspace({
  session,
  end,
}: {
  session: Session;
  end: (notice?: string) => void;
}) {
  const controller = useRef(new AbortController()),
    alive = useRef(true),
    urls = useRef(new Set<string>());
  const api = useRef(
    new Api(session.access_token, controller.current.signal),
  ).current;
  const [tab, setTab] = useState<"documents" | "ask">("documents"),
    [docs, setDocs] = useState<Document[]>([]),
    [total, setTotal] = useState(0),
    [offset, setOffset] = useState(0),
    [loading, setLoading] = useState(true),
    [hasLoaded, setHasLoaded] = useState(false),
    [showExamples, setShowExamples] = useState(false),
    [error, setError] = useState(""),
    [service, setService] = useState("Checking service"),
    [providerOff, setProviderOff] = useState(false),
    [simulated, setSimulated] = useState(false);
  const [uploading, setUploading] = useState(false),
    [uploadNote, setUploadNote] = useState(""),
    [question, setQuestion] = useState(""),
    [answer, setAnswer] = useState<Answer | null>(null),
    [asking, setAsking] = useState(false),
    [queryError, setQueryError] = useState(""),
    [downloading, setDownloading] = useState("");
  const uploadBusy = useRef(false);
  const questionInput = useRef<HTMLTextAreaElement>(null);
  const uploadKeys = useRef(new Map<string, string>());
  const jobs = useRef(new Map<string, Status>()),
    input = useRef<HTMLInputElement>(null),
    listVersion = useRef(0);
  function fail(e: unknown, set: (v: string) => void) {
    if (!alive.current) return;
    if (e instanceof ApiError && e.status === 401) {
      end("Your session has expired. Sign in again.");
      return;
    }
    if (e instanceof ApiError && e.providerDisabled) setProviderOff(true);
    set(message(e));
  }
  async function refresh(page = offset) {
    const version = ++listVersion.current;
    setLoading(true);
    try {
      const result = await api.documents(page);
      if (alive.current && version === listVersion.current) {
        setDocs(result.documents);
        setHasLoaded(true);
        setTotal(result.total);
        setError("");
        result.documents
          .filter((d) => !terminal(d.status))
          .forEach((d) => jobs.current.set(d.job_id, d.status));
      }
    } catch (e) {
      fail(e, setError);
    } finally {
      if (alive.current && version === listVersion.current) setLoading(false);
    }
  }
  useEffect(() => {
    alive.current = true;
    void refresh(offset);
    return () => {
      listVersion.current++;
    };
  }, [offset]); // Only manual navigation or actions refresh the list.
  useEffect(() => {
    const timer = setTimeout(
      () => end("Your session has expired. Sign in again."),
      session.expires_in * 1000,
    );
    fetch("/health", {
      signal: controller.current.signal,
      cache: "no-store",
      credentials: "omit",
    })
      .then((r) => {
        if (alive.current) {
          setService(r.ok ? "API online" : "API unavailable");
          setSimulated(
            r.ok && r.headers.get("X-Demo-Provider-Mode") === "deterministic",
          );
        }
      })
      .catch(() => {
        if (alive.current) setService("API unavailable");
      });
    return () => {
      alive.current = false;
      controller.current.abort();
      clearTimeout(timer);
      urls.current.forEach((u) => URL.revokeObjectURL(u));
      urls.current.clear();
      jobs.current.clear();
    };
  }, []);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (stopped) return;
      for (const [id] of jobs.current) {
        if (stopped) break;
        try {
          const job = await api.job(id);
          if (stopped || !alive.current) return;
          if (terminal(job.status)) {
            jobs.current.delete(id);
            setUploadNote(
              job.status === "completed"
                ? "Document is ready for questions."
                : "Ingestion failed. The source PDF is still available.",
            );
            void refresh(offset);
          } else {
            jobs.current.set(id, job.status);
            setDocs((current) =>
              current.map((d) =>
                d.job_id === id ? { ...d, status: job.status } : d,
              ),
            );
          }
        } catch (e) {
          if (stopped) return;
          jobs.current.delete(id);
          fail(e, setError);
        }
      }
      if (!stopped) timer = setTimeout(poll, 2000);
    }
    timer = setTimeout(poll, 2000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [offset]);
  function receiveFiles(files: File[]) {
    if (uploading || uploadBusy.current || providerOff) return;
    if (files.length !== 1) {
      setUploadNote("Choose exactly one PDF at a time.");
      return;
    }
    void upload(files[0]);
  }
  async function upload(file?: File) {
    if (!file || uploading || uploadBusy.current || providerOff) return;
    setUploadNote("");
    if (file.size > 5 * 1024 * 1024) {
      setUploadNote("Choose a PDF smaller than or equal to 5 MiB.");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".pdf") || file.size === 0) {
      setUploadNote("Choose a non-empty PDF document.");
      return;
    }
    uploadBusy.current = true;
    setUploading(true);
    try {
      const digest = Array.from(
        new Uint8Array(
          await crypto.subtle.digest("SHA-256", await file.arrayBuffer()),
        ),
        (b) => b.toString(16).padStart(2, "0"),
      ).join("");
      if (!alive.current) return;
      const key = uploadKeys.current.get(digest) ?? crypto.randomUUID();
      uploadKeys.current.set(digest, key);
      const result = await api.upload(file, key);
      if (!alive.current) return;
      if (!terminal(result.status))
        jobs.current.set(result.job_id, result.status);
      setUploadNote(
        result.status === "completed"
          ? "Document is ready for questions."
          : result.status === "failed"
            ? "Ingestion failed. The source PDF is still available."
            : "PDF accepted. Tracking ingestion status.",
      );
      void refresh(offset);
    } catch (e) {
      if (e instanceof ApiError && e.code === "submission_unknown" && e.jobId) {
        jobs.current.set(e.jobId, "submission_unknown");
        if (alive.current)
          setUploadNote(
            "Submission confirmation is pending. Tracking this job; do not upload it again.",
          );
      } else fail(e, setUploadNote);
    } finally {
      uploadBusy.current = false;
      if (alive.current) setUploading(false);
      if (input.current) input.current.value = "";
    }
  }
  async function ask(e: FormEvent) {
    e.preventDefault();
    if (asking || providerOff || !question.trim() || question.length > 2000)
      return;
    setAsking(true);
    setQueryError("");
    setAnswer(null);
    try {
      const result = await api.query(question);
      if (alive.current) setAnswer(result);
    } catch (e) {
      fail(e, setQueryError);
    } finally {
      if (alive.current) setAsking(false);
    }
  }
  async function download(id: string, filename: string) {
    if (downloading) return;
    setDownloading(id);
    try {
      const blob = await api.pdf(id);
      if (!alive.current) return;
      const url = URL.createObjectURL(blob);
      urls.current.add(url);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click(); // Authenticated fetch, never a token-bearing URL.
      setTimeout(() => {
        URL.revokeObjectURL(url);
        urls.current.delete(url);
      }, 1000);
    } catch (e) {
      fail(e, setError);
    } finally {
      if (alive.current) setDownloading("");
    }
  }
  const tenant = session.user.tenant_id.toUpperCase();
  return (
    <div className="shell">
      <a className="skip-link" href="#workspace-content">
        Skip to workspace
      </a>
      <aside className="sidebar">
        <div className="brand">
          <Layers3 /> atlas<span> / rag</span>
        </div>
        <div className="workspace-label">WORKSPACE</div>
        <div className="tenant">
          <div className="tenant-avatar">{tenant.slice(0, 1)}</div>
          <div>
            <strong>{tenant}</strong>
            <span>Private workspace</span>
          </div>
          <ShieldCheck size={16} />
        </div>
        <nav aria-label="Workspace navigation">
          <button
            aria-current={tab === "documents" ? "page" : undefined}
            className={tab === "documents" ? "active" : ""}
            onClick={() => setTab("documents")}
          >
            <FileText size={19} /> Documents{" "}
            <span>{hasLoaded ? total : "—"}</span>
          </button>
          <button
            aria-current={tab === "ask" ? "page" : undefined}
            className={tab === "ask" ? "active" : ""}
            onClick={() => setTab("ask")}
          >
            <Sparkles size={19} /> Ask AI <ArrowUpRight size={16} />
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="isolation">
            <ShieldCheck size={19} />
            <strong>Built around your boundaries</strong>
            <p>Only your tenant’s documents and sources appear here.</p>
          </div>
          <button
            className="session"
            aria-label="Switch account / sign out"
            title="Switch account / sign out"
            onClick={() => {
              controller.current.abort();
              end();
            }}
          >
            <div className="user-avatar">{tenant.slice(0, 1)}</div>
            <span>
              <strong>{session.user.user_id}</strong>
              <small>Switch account / sign out</small>
            </span>
            <LogOut size={17} />
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <span>
            Workspace <ChevronRight size={13} />{" "}
            <strong>{tab === "documents" ? "Documents" : "Ask AI"}</strong>
          </span>
          <div className="service">
            <i className={service === "API online" ? "online" : ""} />
            {service}
            <span className="local">LOCAL DEMO</span>
          </div>
        </header>
        <main className="content" id="workspace-content" tabIndex={-1}>
          <div className="page-heading">
            <div>
              <div className="eyebrow">{tenant} KNOWLEDGE WORKSPACE</div>
              <h1>
                {tab === "documents"
                  ? "A home for your knowledge."
                  : "Good questions. Grounded answers."}
              </h1>
              <p>
                {tab === "documents"
                  ? "Bring your documents together. Make every answer traceable."
                  : "Ask about your documents and follow the evidence back to its source."}
              </p>
            </div>
            {tab === "documents" && (
              <button
                className="primary"
                disabled={uploading || providerOff}
                onClick={() => input.current?.click()}
              >
                <Plus size={18} /> Upload PDF
              </button>
            )}
          </div>
          <div className="mode-banner">
            <span className="mode-icon">
              <ShieldCheck size={21} />
            </span>
            <div>
              <strong>
                {providerOff
                  ? "Provider disabled"
                  : simulated
                    ? "Simulated AI — deterministic local demo"
                    : "A controlled, isolated demo"}
              </strong>
              <p>
                {providerOff
                  ? "AI queries and ingestion are unavailable. Your documents can still be listed and downloaded."
                  : simulated
                    ? "Local lexical retrieval and source excerpts. No LLM, external inference, or provider charges. Verify each cited PDF page."
                    : "Provider access is disabled by default. AI and ingestion require an explicitly enabled backend; this dashboard never enables them."}
              </p>
            </div>
            <span className="pill">
              {simulated && !providerOff
                ? "LOCAL SIMULATION"
                : "SAFE BY DEFAULT"}
            </span>
          </div>
          {error && <Alert text={error} />}
          {tab === "documents" ? (
            <DocumentLibrary
              documents={docs}
              total={total}
              offset={offset}
              loading={loading}
              hasLoaded={hasLoaded}
              error={error}
              uploading={uploading}
              providerOff={providerOff}
              uploadNote={uploadNote}
              downloading={downloading}
              fileInput={input}
              onFiles={receiveFiles}
              onRefresh={() => void refresh()}
              onPage={(next) => {
                listVersion.current++;
                setDocs([]);
                setHasLoaded(false);
                setLoading(true);
                setError("");
                setOffset(next);
              }}
              onDismissNote={() => setUploadNote("")}
              onDownload={(id, filename) => void download(id, filename)}
            />
          ) : (
            <section className="ask-layout">
              <div className="ask-card">
                <span className="ask-mark">
                  <Sparkles size={25} />
                </span>
                <h2>What would you like to know?</h2>
                <p>Answers are grounded in {tenant}’s indexed documents.</p>
                <form onSubmit={ask}>
                  <label className="sr-only" htmlFor="question">
                    Your question
                  </label>
                  <textarea
                    ref={questionInput}
                    rows={2}
                    aria-describedby="question-counter question-help"
                    onKeyDown={(event) => {
                      if (
                        event.key === "Enter" &&
                        (event.ctrlKey || event.metaKey)
                      ) {
                        event.preventDefault();
                        event.currentTarget.form?.requestSubmit();
                      }
                    }}
                    id="question"
                    placeholder="Ask a question about your documents…"
                    value={question}
                    maxLength={2000}
                    onChange={(e) => setQuestion(e.target.value)}
                    disabled={asking}
                  />
                  <div className="composer-footer">
                    <span id="question-counter">{question.length} / 2,000</span>
                    <button
                      aria-label="Ask question"
                      className="primary"
                      disabled={asking || !question.trim() || providerOff}
                    >
                      {asking ? (
                        <>
                          <LoaderCircle className="spin" size={16} /> Finding
                          evidence…
                        </>
                      ) : (
                        <>
                          Ask AI <ArrowRight size={16} />
                        </>
                      )}
                    </button>
                  </div>
                </form>
                <div className="question-examples">
                  <button
                    type="button"
                    className="example-toggle"
                    aria-expanded={showExamples}
                    aria-controls="example-questions"
                    onClick={() => setShowExamples((value) => !value)}
                  >
                    Example questions{" "}
                    <ChevronDown size={14} aria-hidden="true" />
                  </button>
                  {showExamples && (
                    <div id="example-questions">
                      <p>Edit a suggestion to match your documents.</p>
                      <div className="example-chips">
                        {[
                          "What is the remote work allowance?",
                          "What is the annual leave allowance?",
                          "What is the reimbursement limit?",
                        ].map((text) => (
                          <button
                            type="button"
                            key={text}
                            disabled={asking || providerOff}
                            onClick={() => {
                              setQuestion(text);
                              questionInput.current?.focus();
                            }}
                          >
                            {text}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                <small id="question-help">
                  One request at a time. Demo rate and usage limits apply.{" "}
                  <kbd>⌘ / Ctrl + Enter</kbd> to submit.
                </small>
              </div>
              {queryError && <Alert text={queryError} />}{" "}
              <AnswerPanel
                answer={answer}
                asking={asking}
                hasError={!!queryError}
                downloading={downloading}
                onDownload={(id, filename) => void download(id, filename)}
                onEdit={() => questionInput.current?.focus()}
              />
            </section>
          )}
          <footer className="footer">
            <span>
              <Layers3 size={14} /> ATLAS / ENTERPRISE RAG
            </span>
            <span>Private by design. Grounded in your documents.</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
