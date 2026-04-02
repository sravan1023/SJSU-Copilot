var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// src/index.ts
var CORS_HEADERS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "content-type, authorization",
  "access-control-allow-methods": "GET, POST, OPTIONS"
};
var SOURCES = [
  { name: "RemoteOK", type: "remoteok", url: "https://remoteok.com/api" },
  { name: "Remotive", type: "remotive", url: "https://remotive.com/api/remote-jobs" },
  { name: "Arbeitnow", type: "arbeitnow", url: "https://www.arbeitnow.com/api/job-board-api" }
];
var json = /* @__PURE__ */ __name((data, init = {}) => new Response(JSON.stringify(data), {
  ...init,
  headers: {
    "content-type": "application/json; charset=utf-8",
    ...CORS_HEADERS,
    ...init.headers || {}
  }
}), "json");
var parseNumber = /* @__PURE__ */ __name((value, fallback) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}, "parseNumber");
var normalizeRemoteStatus = /* @__PURE__ */ __name((text) => {
  const value = text.toLowerCase();
  if (value.includes("remote")) return "remote";
  if (value.includes("hybrid")) return "hybrid";
  if (value.includes("onsite") || value.includes("on-site")) return "onsite";
  return "unknown";
}, "normalizeRemoteStatus");
var normalizeJobType = /* @__PURE__ */ __name((text) => {
  const value = text.toLowerCase();
  if (value.includes("intern")) return "internship";
  if (value.includes("new grad") || value.includes("new-grad") || value.includes("entry")) return "new_grad";
  if (value.includes("part")) return "part_time";
  if (value.includes("contract")) return "contract";
  if (value.includes("full")) return "full_time";
  return "other";
}, "normalizeJobType");
var cleanHtml = /* @__PURE__ */ __name((input) => input.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim(), "cleanHtml");
var roleMatches = /* @__PURE__ */ __name((job, role) => {
  if (!role) return true;
  const haystack = `${job.title} ${job.company} ${job.description || ""} ${(job.tags || []).join(" ")}`.toLowerCase();
  return role.toLowerCase().split(/\s+/).filter((token) => token.length > 2).some((token) => haystack.includes(token));
}, "roleMatches");
var experienceMatches = /* @__PURE__ */ __name((job, experienceLevel) => {
  if (!experienceLevel) return true;
  const haystack = `${job.title} ${job.description || ""} ${(job.tags || []).join(" ")}`.toLowerCase();
  if (experienceLevel === "intern") return haystack.includes("intern");
  if (experienceLevel === "new-grad") return ["new grad", "graduate", "entry", "junior", "associate"].some((kw) => haystack.includes(kw));
  return ["entry", "junior", "new grad", "associate", "early career", "graduate"].some((kw) => haystack.includes(kw));
}, "experienceMatches");
var hashId = /* @__PURE__ */ __name(async (input) => {
  const bytes = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-1", bytes);
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 20);
}, "hashId");
var buildDedupeHash = /* @__PURE__ */ __name(async (title, company) => hashId(`${title.toLowerCase().trim()}|${company.toLowerCase().trim()}`), "buildDedupeHash");
var normalizeRemoteOk = /* @__PURE__ */ __name(async (rows, source) => {
  return Promise.all(
    rows.slice(1).map(async (row) => ({
      id: String(row.id || row.slug || ""),
      dedupe_hash: await buildDedupeHash(String(row.position || row.title || ""), String(row.company || "")),
      title: row.position || row.title || "",
      company: row.company || "",
      location: row.location || "Remote",
      remote_status: normalizeRemoteStatus(`${row.location || ""} remote`),
      job_type: normalizeJobType(`${row.tags || []} ${row.position || ""}`),
      salary: null,
      experience_level: null,
      tags: Array.isArray(row.tags) ? row.tags.map((tag) => tag.toLowerCase()) : [],
      description: cleanHtml(row.description || ""),
      apply_url: row.url || `https://remoteok.com/${row.slug || row.id}`,
      source_name: source.name,
      source_url: source.url,
      posted_date: row.date ? String(row.date).slice(0, 10) : null,
      fetched_at: (/* @__PURE__ */ new Date()).toISOString()
    }))
  );
}, "normalizeRemoteOk");
var normalizeRemotive = /* @__PURE__ */ __name(async (body, source) => {
  const jobs = Array.isArray(body.jobs) ? body.jobs : [];
  return Promise.all(
    jobs.map(async (row) => ({
      id: String(row.id || row.url || ""),
      title: row.title || "",
      company: row.company_name || "",
      location: row.candidate_required_location || "Remote",
      remote_status: normalizeRemoteStatus(`${row.candidate_required_location || ""} remote`),
      job_type: normalizeJobType(`${row.job_type || ""} ${row.title || ""}`),
      salary: row.salary || null,
      experience_level: null,
      tags: Array.isArray(row.tags) ? row.tags.map((tag) => tag.toLowerCase()) : [],
      description: cleanHtml(row.description || ""),
      apply_url: row.url || "",
      dedupe_hash: await buildDedupeHash(String(row.title || ""), String(row.company_name || "")),
      source_name: source.name,
      source_url: source.url,
      posted_date: row.publication_date ? String(row.publication_date).slice(0, 10) : null,
      fetched_at: (/* @__PURE__ */ new Date()).toISOString()
    }))
  );
}, "normalizeRemotive");
var normalizeArbeitnow = /* @__PURE__ */ __name(async (body, source) => {
  const jobs = Array.isArray(body.data) ? body.data : [];
  return Promise.all(
    jobs.map(async (row) => ({
      id: String(row.slug || row.url || ""),
      title: row.title || "",
      company: row.company_name || "",
      location: row.location || "Remote",
      remote_status: normalizeRemoteStatus(`${row.location || ""} remote`),
      job_type: normalizeJobType(`${row.title || ""} ${row.tags || []}`),
      salary: null,
      experience_level: null,
      tags: Array.isArray(row.tags) ? row.tags.map((tag) => tag.toLowerCase()) : [],
      description: cleanHtml(row.description || ""),
      apply_url: row.url || "",
      dedupe_hash: await buildDedupeHash(String(row.title || ""), String(row.company_name || "")),
      source_name: source.name,
      source_url: source.url,
      posted_date: row.created_at ? String(row.created_at).slice(0, 10) : null,
      fetched_at: (/* @__PURE__ */ new Date()).toISOString()
    }))
  );
}, "normalizeArbeitnow");
var fetchSource = /* @__PURE__ */ __name(async (source) => {
  const response = await fetch(source.url, {
    headers: {
      "user-agent": "SJSU-Job-Fetcher/1.0",
      accept: "application/json,text/plain,*/*"
    }
  });
  if (!response.ok) throw new Error(`${source.name} failed (${response.status})`);
  const body = await response.json();
  switch (source.type) {
    case "remoteok":
      return normalizeRemoteOk(body, source);
    case "remotive":
      return normalizeRemotive(body, source);
    case "arbeitnow":
      return normalizeArbeitnow(body, source);
    default:
      return [];
  }
}, "fetchSource");
var dedupeJobs = /* @__PURE__ */ __name((jobs) => {
  const seen = /* @__PURE__ */ new Set();
  const unique = [];
  for (const job of jobs) {
    const key = `${job.apply_url.toLowerCase()}|${job.title.toLowerCase()}|${job.company.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(job);
  }
  return unique;
}, "dedupeJobs");
var filterJobs = /* @__PURE__ */ __name((jobs, query) => {
  const role = query.role?.trim();
  const level = query.experienceLevel?.trim();
  return jobs.filter((job) => roleMatches(job, role) && experienceMatches(job, level));
}, "filterJobs");
async function handleFetch(request) {
  const url = new URL(request.url);
  const query = {
    role: url.searchParams.get("role") || void 0,
    count: parseNumber(url.searchParams.get("count"), 30),
    experienceLevel: url.searchParams.get("experienceLevel") || void 0
  };
  const sourceResults = await Promise.allSettled(SOURCES.map((source) => fetchSource(source)));
  const jobs = sourceResults.flatMap((result, index) => result.status === "fulfilled" ? result.value : []).filter(Boolean);
  const filtered = dedupeJobs(filterJobs(jobs, query)).slice(0, Math.max(1, Math.min(100, query.count || 30)));
  return json({
    query,
    sourceCount: SOURCES.length,
    jobs: filtered,
    fetched_at: (/* @__PURE__ */ new Date()).toISOString(),
    sources: SOURCES.map((source, index) => ({
      name: source.name,
      ok: sourceResults[index].status === "fulfilled",
      error: sourceResults[index].status === "rejected" ? String(sourceResults[index].reason) : null
    }))
  });
}
__name(handleFetch, "handleFetch");
var src_default = {
  async fetch(request) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS_HEADERS });
    }
    const url = new URL(request.url);
    if (url.pathname === "/fetch" && request.method === "GET") {
      try {
        return await handleFetch(request);
      } catch (error) {
        return json({ error: error instanceof Error ? error.message : "Worker fetch failed" }, { status: 500 });
      }
    }
    return json({ ok: true, name: "sjsu-job-fetcher" });
  }
};

// node_modules/wrangler/templates/middleware/middleware-ensure-req-body-drained.ts
var drainBody = /* @__PURE__ */ __name(async (request, env, _ctx, middlewareCtx) => {
  try {
    return await middlewareCtx.next(request, env);
  } finally {
    try {
      if (request.body !== null && !request.bodyUsed) {
        const reader = request.body.getReader();
        while (!(await reader.read()).done) {
        }
      }
    } catch (e) {
      console.error("Failed to drain the unused request body.", e);
    }
  }
}, "drainBody");
var middleware_ensure_req_body_drained_default = drainBody;

// node_modules/wrangler/templates/middleware/middleware-miniflare3-json-error.ts
function reduceError(e) {
  return {
    name: e?.name,
    message: e?.message ?? String(e),
    stack: e?.stack,
    cause: e?.cause === void 0 ? void 0 : reduceError(e.cause)
  };
}
__name(reduceError, "reduceError");
var jsonError = /* @__PURE__ */ __name(async (request, env, _ctx, middlewareCtx) => {
  try {
    return await middlewareCtx.next(request, env);
  } catch (e) {
    const error = reduceError(e);
    return Response.json(error, {
      status: 500,
      headers: { "MF-Experimental-Error-Stack": "true" }
    });
  }
}, "jsonError");
var middleware_miniflare3_json_error_default = jsonError;

// .wrangler/tmp/bundle-c6vyen/middleware-insertion-facade.js
var __INTERNAL_WRANGLER_MIDDLEWARE__ = [
  middleware_ensure_req_body_drained_default,
  middleware_miniflare3_json_error_default
];
var middleware_insertion_facade_default = src_default;

// node_modules/wrangler/templates/middleware/common.ts
var __facade_middleware__ = [];
function __facade_register__(...args) {
  __facade_middleware__.push(...args.flat());
}
__name(__facade_register__, "__facade_register__");
function __facade_invokeChain__(request, env, ctx, dispatch, middlewareChain) {
  const [head, ...tail] = middlewareChain;
  const middlewareCtx = {
    dispatch,
    next(newRequest, newEnv) {
      return __facade_invokeChain__(newRequest, newEnv, ctx, dispatch, tail);
    }
  };
  return head(request, env, ctx, middlewareCtx);
}
__name(__facade_invokeChain__, "__facade_invokeChain__");
function __facade_invoke__(request, env, ctx, dispatch, finalMiddleware) {
  return __facade_invokeChain__(request, env, ctx, dispatch, [
    ...__facade_middleware__,
    finalMiddleware
  ]);
}
__name(__facade_invoke__, "__facade_invoke__");

// .wrangler/tmp/bundle-c6vyen/middleware-loader.entry.ts
var __Facade_ScheduledController__ = class ___Facade_ScheduledController__ {
  constructor(scheduledTime, cron, noRetry) {
    this.scheduledTime = scheduledTime;
    this.cron = cron;
    this.#noRetry = noRetry;
  }
  static {
    __name(this, "__Facade_ScheduledController__");
  }
  #noRetry;
  noRetry() {
    if (!(this instanceof ___Facade_ScheduledController__)) {
      throw new TypeError("Illegal invocation");
    }
    this.#noRetry();
  }
};
function wrapExportedHandler(worker) {
  if (__INTERNAL_WRANGLER_MIDDLEWARE__ === void 0 || __INTERNAL_WRANGLER_MIDDLEWARE__.length === 0) {
    return worker;
  }
  for (const middleware of __INTERNAL_WRANGLER_MIDDLEWARE__) {
    __facade_register__(middleware);
  }
  const fetchDispatcher = /* @__PURE__ */ __name(function(request, env, ctx) {
    if (worker.fetch === void 0) {
      throw new Error("Handler does not export a fetch() function.");
    }
    return worker.fetch(request, env, ctx);
  }, "fetchDispatcher");
  return {
    ...worker,
    fetch(request, env, ctx) {
      const dispatcher = /* @__PURE__ */ __name(function(type, init) {
        if (type === "scheduled" && worker.scheduled !== void 0) {
          const controller = new __Facade_ScheduledController__(
            Date.now(),
            init.cron ?? "",
            () => {
            }
          );
          return worker.scheduled(controller, env, ctx);
        }
      }, "dispatcher");
      return __facade_invoke__(request, env, ctx, dispatcher, fetchDispatcher);
    }
  };
}
__name(wrapExportedHandler, "wrapExportedHandler");
function wrapWorkerEntrypoint(klass) {
  if (__INTERNAL_WRANGLER_MIDDLEWARE__ === void 0 || __INTERNAL_WRANGLER_MIDDLEWARE__.length === 0) {
    return klass;
  }
  for (const middleware of __INTERNAL_WRANGLER_MIDDLEWARE__) {
    __facade_register__(middleware);
  }
  return class extends klass {
    #fetchDispatcher = /* @__PURE__ */ __name((request, env, ctx) => {
      this.env = env;
      this.ctx = ctx;
      if (super.fetch === void 0) {
        throw new Error("Entrypoint class does not define a fetch() function.");
      }
      return super.fetch(request);
    }, "#fetchDispatcher");
    #dispatcher = /* @__PURE__ */ __name((type, init) => {
      if (type === "scheduled" && super.scheduled !== void 0) {
        const controller = new __Facade_ScheduledController__(
          Date.now(),
          init.cron ?? "",
          () => {
          }
        );
        return super.scheduled(controller);
      }
    }, "#dispatcher");
    fetch(request) {
      return __facade_invoke__(
        request,
        this.env,
        this.ctx,
        this.#dispatcher,
        this.#fetchDispatcher
      );
    }
  };
}
__name(wrapWorkerEntrypoint, "wrapWorkerEntrypoint");
var WRAPPED_ENTRY;
if (typeof middleware_insertion_facade_default === "object") {
  WRAPPED_ENTRY = wrapExportedHandler(middleware_insertion_facade_default);
} else if (typeof middleware_insertion_facade_default === "function") {
  WRAPPED_ENTRY = wrapWorkerEntrypoint(middleware_insertion_facade_default);
}
var middleware_loader_entry_default = WRAPPED_ENTRY;
export {
  __INTERNAL_WRANGLER_MIDDLEWARE__,
  middleware_loader_entry_default as default
};
//# sourceMappingURL=index.js.map
