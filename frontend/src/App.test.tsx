import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const session = (tenant = "acme") => ({
  access_token: "synthetic-" + tenant,
  expires_in: 900,
  user: { tenant_id: tenant, user_id: tenant + "-demo", role: "admin" },
});
const doc = {
  document_id: "doc-owned",
  job_id: "job-owned",
  filename: "policy.pdf",
  size_bytes: 100,
  page_count: 2,
  created_at: "2026-01-01T00:00:00Z",
  status: "queued",
};
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.stubGlobal("crypto", {
    randomUUID: () => "00000000-0000-4000-8000-000000000001",
    subtle: { digest: vi.fn(async () => new ArrayBuffer(32)) },
  });
  Object.defineProperty(File.prototype, "arrayBuffer", {
    configurable: true,
    value: async () => new ArrayBuffer(1),
  });
  localStorage.clear();
  sessionStorage.clear();
  fetchMock = vi.fn(async (path: string) =>
    path === "/health"
      ? new Response("ok")
      : json({ documents: [], total: 0, limit: 20, offset: 0 }),
  );
  vi.stubGlobal("fetch", fetchMock);
});
async function login(tenant = "acme", mode?: string) {
  fetchMock.mockImplementation(async (path: string) =>
    path.includes("/demo/session")
      ? json(session(tenant))
      : path === "/health"
        ? new Response("ok", {
            headers: mode ? { "X-Demo-Provider-Mode": mode } : {},
          })
        : json({ documents: [], total: 0, limit: 20, offset: 0 }),
  );
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "test-password" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: /Enter workspace/ }).closest("form")!,
  );
  await screen.findByText("A home for your knowledge.");
  await waitFor(() =>
    expect(
      screen.queryByLabelText("Loading documents"),
    ).not.toBeInTheDocument(),
  );
}
describe("dashboard workflows", () => {
  it("logs in and stores no session or password in browser storage", async () => {
    render(<App />);
    await login();
    expect(screen.getByText("ACME")).toBeInTheDocument();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });
  it("shows generic invalid credentials", async () => {
    fetchMock.mockResolvedValue(json({ detail: "private" }, 401));
    render(<App />);
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "bad" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Enter workspace/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Account or password is incorrect.",
    );
  });
  it("validates file size before HTTP upload", async () => {
    render(<App />);
    await login();
    const file = new File(["x"], "too-big.pdf", { type: "application/pdf" });
    Object.defineProperty(file, "size", { value: 5242881 });
    fireEvent.change(screen.getByLabelText("Select PDF"), {
      target: { files: [file] },
    });
    expect(await screen.findByRole("status")).toHaveTextContent("5 MiB");
    expect(
      fetchMock.mock.calls.filter((c) => c[1]?.method === "POST"),
    ).toHaveLength(1);
  });
  it("uploads once and polls at two seconds until terminal status", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<App />);
    await login();
    let completed = false;
    fetchMock.mockImplementation(async (path: string) =>
      path.includes("ingestion-jobs")
        ? ((completed = true),
          json({ ...doc, status: "completed", error: null }))
        : path.startsWith("/api/v1/documents?")
          ? json({
              documents: [
                { ...doc, status: completed ? "completed" : "queued" },
              ],
              total: 1,
              limit: 20,
              offset: 0,
            })
          : json({ ...doc, status: "queued" }),
    );
    fireEvent.change(screen.getByLabelText("Select PDF"), {
      target: {
        files: [new File(["%PDF"], "policy.pdf", { type: "application/pdf" })],
      },
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent("PDF accepted");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByText("Ready")).toBeInTheDocument();
    const count = fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes("ingestion-jobs"),
    ).length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(
      fetchMock.mock.calls.filter((c) =>
        String(c[0]).includes("ingestion-jobs"),
      ),
    ).toHaveLength(count);
  });
  it("renders grounded answer and structured citation with authorized PDF fetch", async () => {
    render(<App />);
    await login();
    const create = vi.fn(() => "blob:synthetic"),
      revoke = vi.fn();
    vi.stubGlobal(
      "URL",
      Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }),
    );
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    fetchMock.mockImplementation(async (path: string) =>
      path.endsWith("/file")
        ? new Response("%PDF", {
            headers: { "Content-Type": "application/pdf" },
          })
        : json({
            answer: "Allowance is 500 USD [1].",
            citations: [
              {
                citation_id: 1,
                sources: [
                  {
                    document_id: "doc-owned",
                    filename: "acme-policy.pdf",
                    page: 2,
                    chunk_id: "chunk",
                  },
                ],
              },
            ],
          }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Ask AI/ }));
    fireEvent.change(screen.getByLabelText("Your question"), {
      target: { value: "What is the allowance?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask question/ }));
    expect(
      await screen.findByText("Allowance is 500 USD [1]."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /acme-policy.pdf/ }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(
      fetchMock.mock.calls.some(
        (c) => c[0] === "/api/v1/documents/doc-owned/file",
      ),
    ).toBe(true);
    expect(click).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Switch account/ }));
    expect(revoke).toHaveBeenCalledWith("blob:synthetic");
  });
  it("displays provider-disabled truthfully and prevents repeated actions", async () => {
    render(<App />);
    await login();
    fetchMock.mockResolvedValue(
      json({ detail: "Demo provider is disabled" }, 503),
    );
    fireEvent.click(screen.getByRole("button", { name: /Ask AI/ }));
    fireEvent.change(screen.getByLabelText("Your question"), {
      target: { value: "Question" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask question/ }));
    await screen.findByText("Provider disabled");
    expect(screen.getByRole("button", { name: /Ask question/ })).toBeDisabled();
    expect(screen.queryByText("500 USD")).not.toBeInTheDocument();
  });
  it("expires a session on 401", async () => {
    render(<App />);
    await login();
    fetchMock.mockResolvedValue(json({}, 401));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(
      await screen.findByText("Your session has expired. Sign in again."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });
  it("rejects late old-tenant responses after switching accounts and stops polling", async () => {
    render(<App />);
    await login();
    let resolve!: (value: Response) => void;
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((r) => {
          resolve = r;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    const oldSignal = fetchMock.mock.calls.at(-1)![1].signal;
    fireEvent.click(screen.getByRole("button", { name: /Switch account/ }));
    expect(oldSignal.aborted).toBe(true);
    fireEvent.change(screen.getByLabelText("Demo account"), {
      target: { value: "globex-demo" },
    });
    await login("globex");
    await act(async () =>
      resolve(
        json({
          documents: [{ ...doc, filename: "OLD ACME PRIVATE.pdf" }],
          total: 1,
          limit: 20,
          offset: 0,
        }),
      ),
    );
    expect(screen.getByText("GLOBEX")).toBeInTheDocument();
    expect(screen.queryByText("OLD ACME PRIVATE.pdf")).not.toBeInTheDocument();
  });
  it("shows rate-limit feedback without automatically retrying query", async () => {
    render(<App />);
    await login();
    fetchMock.mockResolvedValue(json({}, 429));
    fireEvent.click(screen.getByRole("button", { name: /Ask AI/ }));
    fireEvent.change(screen.getByLabelText("Your question"), {
      target: { value: "question" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask question/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Demo limit reached",
    );
    expect(
      fetchMock.mock.calls.filter((c) => c[0] === "/api/v1/query"),
    ).toHaveLength(1);
  });
});

it('stops job polling and discards a late query after sign-out',async()=>{
 vi.useFakeTimers({shouldAdvanceTime:true});render(<App/>);await login();
 fetchMock.mockResolvedValue(json({documents:[doc],total:1,limit:20,offset:0}));
 fireEvent.click(screen.getByRole('button',{name:'Refresh'}));
 await act(async()=>{await vi.advanceTimersByTimeAsync(100);});
 let resolve!:(value:Response)=>void;
 fetchMock.mockImplementation((path:string)=>path==='/api/v1/query'?new Promise<Response>(r=>{resolve=r;}):Promise.resolve(json({...doc,error:null})));
 fireEvent.click(screen.getByRole('button',{name:/Ask AI/}));fireEvent.change(screen.getByLabelText('Your question'),{target:{value:'private question'}});fireEvent.click(screen.getByRole('button',{name:'Ask question'}));
 fireEvent.click(screen.getByRole('button',{name:/Switch account/}));
 const calls=fetchMock.mock.calls.length;
 await act(async()=>{resolve(json({answer:'OLD TENANT ANSWER',citations:[]}));await vi.advanceTimersByTimeAsync(6000);});
 expect(fetchMock.mock.calls).toHaveLength(calls);expect(screen.queryByText('OLD TENANT ANSWER')).not.toBeInTheDocument();expect(screen.getByLabelText('Password')).toHaveValue('');
});
it('expires the in-memory session without a provider request',async()=>{
 vi.useFakeTimers({shouldAdvanceTime:true});render(<App/>);await login();
 await act(async()=>{await vi.advanceTimersByTimeAsync(900000);});
 expect(screen.getByText('Your session has expired. Sign in again.')).toBeInTheDocument();
 expect(fetchMock.mock.calls.filter(c=>c[0]==='/api/v1/query')).toHaveLength(0);
});
it('reuses the upload idempotency key after an uncertain publish',async()=>{
 render(<App/>);await login();fetchMock.mockImplementation(async(path:string)=>path.startsWith('/api/v1/documents?')?json({documents:[],total:0,limit:20,offset:0}):json({code:'submission_unknown',job_id:'job-uncertain'},503));
 const file=new File(['%PDF'],'policy.pdf');
 fireEvent.change(screen.getByLabelText('Select PDF'),{target:{files:[file]}});
 await screen.findByText(/Submission confirmation is pending/);
 fireEvent.change(screen.getByLabelText('Select PDF'),{target:{files:[file]}});
 await waitFor(()=>expect(fetchMock.mock.calls.filter(c=>c[1]?.method==='POST'&&c[0]==='/api/v1/documents')).toHaveLength(2));
 const uploads=fetchMock.mock.calls.filter(c=>c[1]?.method==='POST'&&c[0]==='/api/v1/documents');
 expect(uploads[0][1].headers.get('Idempotency-Key')).toBe(uploads[1][1].headers.get('Idempotency-Key'));
});

it("labels the server-confirmed local simulation without inventing metrics", async () => {
  render(<App />);
  await login("acme", "deterministic");
  expect(
    await screen.findByText("Simulated AI — deterministic local demo"),
  ).toBeInTheDocument();
  expect(screen.getByText("LOCAL SIMULATION")).toBeInTheDocument();
  expect(screen.queryByText(/cache hit/i)).not.toBeInTheDocument();
});
