export type Session = {
  access_token: string;
  expires_in: number;
  user: { user_id: string; tenant_id: string; role: string };
};
export type Status =
  | "submission_unknown"
  | "queued"
  | "processing"
  | "retrying"
  | "completed"
  | "failed";
export type Document = {
  document_id: string;
  job_id: string;
  filename: string;
  size_bytes: number;
  page_count: number;
  created_at: string;
  status: Status;
};
export type DocumentList = {
  documents: Document[];
  total: number;
  limit: number;
  offset: number;
};
export type Submission = {
  document_id: string;
  job_id: string;
  status: Status;
  status_url: string;
};
export type Job = {
  document_id: string;
  job_id: string;
  status: Status;
  error: { code: string; message: string } | null;
};
export type Answer = {
  answer: string;
  citations: {
    citation_id: number;
    sources: {
      document_id: string;
      filename: string;
      page: number;
      chunk_id: string;
    }[];
  }[];
};
export class ApiError extends Error {
  constructor(
    public status: number,
    public code?: string,
    public jobId?: string,
    public providerDisabled = false,
  ) {
    super(
      status === 401
        ? "Your session has expired. Sign in again."
        : status === 429
          ? "Demo limit reached. Please wait before trying again."
          : providerDisabled
            ? "The demo provider is disabled. AI queries and ingestion are unavailable."
            : status === 404
              ? "This document or job is unavailable."
              : status === 413
                ? "This PDF exceeds the upload limit."
                : status === 422
                  ? "Please check your input and try again."
                  : status === 503
                    ? "The service is temporarily unavailable."
                    : status === 504
                      ? "The request timed out. It has not been retried."
                      : "The request could not be completed.",
    );
  }
}
export class Api {
  constructor(
    private token = "",
    private signal?: AbortSignal,
  ) {}
  private async request(path: string, init: RequestInit = {}) {
    const headers = new Headers(init.headers);
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    const response = await fetch(path, {
      ...init,
      headers,
      signal: this.signal,
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
    });
    if (!response.ok) {
      let body: { detail?: unknown; code?: string; job_id?: string } = {};
      try {
        body = await response.json();
      } catch {
        /* Never display untrusted payloads. */
      }
      throw new ApiError(
        response.status,
        body.code,
        body.job_id,
        body.detail === "Demo provider is disabled",
      );
    }
    return response;
  }
  async login(username: string, password: string): Promise<Session> {
    return (
      await this.request("/api/v1/demo/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      })
    ).json();
  }
  async documents(offset = 0): Promise<DocumentList> {
    return (
      await this.request(`/api/v1/documents?limit=20&offset=${offset}`)
    ).json();
  }
  async upload(file: File, key: string): Promise<Submission> {
    const body = new FormData();
    body.append("file", file);
    return (
      await this.request("/api/v1/documents", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body,
      })
    ).json();
  }
  async job(id: string): Promise<Job> {
    return (
      await this.request(`/api/v1/ingestion-jobs/${encodeURIComponent(id)}`)
    ).json();
  }
  async query(query: string): Promise<Answer> {
    return (
      await this.request("/api/v1/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, use_cache: true }),
      })
    ).json();
  }
  async pdf(id: string): Promise<Blob> {
    const response = await this.request(
      `/api/v1/documents/${encodeURIComponent(id)}/file`,
    );
    if (!response.headers.get("Content-Type")?.startsWith("application/pdf"))
      throw new Error("Invalid PDF response");
    return response.blob();
  }
}
export function message(error: unknown) {
  return error instanceof ApiError
    ? error.message
    : "Connection interrupted. Check the service and try again manually.";
}
