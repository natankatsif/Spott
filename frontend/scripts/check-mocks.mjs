// Every mock (src/lib/mocks/**/*.json) must match the backend's contract, ../backend/openapi.json, so a contract
// change on either side fails here instead of in the demo (docs/API.md). Then the rules the schema can't express:
// citations referenced by sentences, previews that exist, a demo admin page that shows every state.
//
//   npm run check:mocks

import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const MOCKS = join(ROOT, "src", "lib", "mocks");
const PUBLIC = join(ROOT, "public");
const CONTRACT = join(ROOT, "..", "backend", "openapi.json");

const spec = JSON.parse(readFileSync(CONTRACT, "utf8"));
const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
ajv.addSchema({ $id: "contract", components: spec.components });

const failures = [];
const fail = (where, message) => failures.push(`${where}: ${message}`);
const check = (where, ok, message) => ok || fail(where, message);
const load = (name) => JSON.parse(readFileSync(join(MOCKS, name), "utf8"));

function matches(name, model) {
  const validate = ajv.getSchema(`contract#/components/schemas/${model}`);
  if (!validate) return fail(name, `no ${model} in ${CONTRACT}`);
  const data = load(name);
  if (!validate(data)) fail(name, `not a ${model}: ${ajv.errorsText(validate.errors, { dataVar: model })}`);
  return data;
}

const subset = (a, b) => [...a].every((x) => b.has(x));

// ─────────────── /api/ask ───────────────
const askMocks = readdirSync(join(MOCKS, "ask")).filter((f) => f.endsWith(".json")).sort();
check("ask/", askMocks.length > 0, "no mocks");
for (const file of askMocks) {
  const name = `ask/${file}`;
  const r = matches(name, "AskResponse");
  const ids = r.citations.map((c) => c.id);
  const idSet = new Set(ids);
  check(name, ids.length === idSet.size, "duplicate citation ids");
  const cited = new Set(r.sentences.flatMap((s) => s.cites));
  for (const step of r.checklist?.steps ?? []) step.cites.forEach((id) => cited.add(id));
  check(name, subset(cited, idSet), "a sentence cites a citation that isn't in the list");
  check(name, subset(idSet, cited), "every citation must be referenced by a sentence or checklist step");
  check(name, r.answer === r.sentences.map((s) => s.text).join(" "), "answer must be the sentences joined");
  for (const c of r.citations) {
    check(name, c.deep_link.startsWith(c.url), `${c.id}: deep_link doesn't start with url`);
    check(name, c.quote_lang === r.lang || c.translation, `${c.id}: quote in ${c.quote_lang}, answer in ${r.lang}, no translation`);
    // the source preview: a real static preview in mock mode (backend/scripts/export_previews.py)
    check(name, c.preview_url.startsWith("/mocks/preview/"), `${c.id}: ${c.preview_url}`);
    check(name, existsSync(join(PUBLIC, c.preview_url)), `${c.id}: public${c.preview_url} missing, run backend/scripts/export_previews.py`);
    const kind = c.file_url ? "pdf" : c.kind === "page" ? "page" : "text";
    check(name, c.preview_kind === kind, `${c.id}: preview_kind ${c.preview_kind}, expected ${kind}`);
  }
  if (r.status === "conflict") check(name, r.conflict && subset(r.conflict.citation_ids, idSet), "conflict without its citations");
  if (["not_found", "refused"].includes(r.status)) check(name, r.citations.length === 0, `${r.status} with citations`);
  for (const c of r.contacts) {
    // who can help: only without a full answer, and a link to where the contact is written
    check(name, ["not_found", "partial"].includes(r.status), `contacts with status ${r.status}`);
    check(name, c.deep_link.startsWith(c.url) && c.line_ids.length > 0, `contact ${c.name}: no deep link or lines`);
  }
}

// ─────────────── the other endpoints ───────────────
const models = {
  "wall.json": "WallResponse",
  "corpus-stats.json": "CorpusStats",
  "suggestions.json": "SuggestionList",
  "admin/session.json": "AdminSession",
  "admin/sources.json": "SourceList",
  "admin/job.json": "Job",
  "admin/jobs.json": "JobList",
  "admin/feedback.json": "FeedbackList",
  "admin/feedback-stats.json": "FeedbackStats",
  "admin/add-source-site.json": "SourceAdded",
  "admin/add-source-document.json": "SourceAdded",
  "admin/add-source-merged.json": "SourceAdded",
  "admin/add-source-blocked.json": "SourceAdded",
  "admin/add-source-unreachable.json": "ApiError",
  "admin/gaps.json": "GapList",
  "admin/gap-recheck.json": "GapRecheck",
};
for (const [name, model] of Object.entries(models)) matches(name, model);

// The demo admin page: all 40 sites of sites.toml, a running job with progress, a failure, a document.
{
  const name = "admin/sources.json";
  const rows = load(name).sources;
  check(name, new Set(rows.filter((r) => r.kind === "site").map((r) => r.site_id)).size === 40, "not 40 sites");
  const states = new Set(rows.map((r) => r.status));
  check(name, subset(["indexed", "pending", "running", "failed", "blocked"], states), "not every state is shown");
  const running = rows.find((r) => r.status === "running");
  check(name, running?.progress && running.progress.percent > 0 && running.progress.percent < 100, "no running job with progress");
  check(name, rows.find((r) => r.status === "failed")?.last_error, "the failed source has no error");
  check(name, rows.some((r) => r.kind === "document"), "no document source");
  const merged = load("admin/add-source-merged.json");
  check("admin/add-source-merged.json", merged.merged_into === merged.id, "merged_into must be its own id");
}

// Shaped like the backend builds it (spott/api/gaps.py): the example is the first question, count = questions,
// the worst status, a group re-checked as answered is not listed.
{
  const name = "admin/gaps.json";
  const gaps = load(name);
  for (const g of gaps.items) {
    check(name, g.example === g.questions[0].question && g.id === g.questions[0].answer_id, `${g.id}: example / id`);
    check(name, g.count === g.questions.length, `${g.id}: count`);
    const worst = g.questions.some((q) => q.status === "not_found") ? "not_found" : "partial";
    check(name, g.status === worst, `${g.id}: status`);
    check(name, !g.rechecked || g.rechecked.status !== "answered", `${g.id}: re-checked as answered`);
  }
  const questions = gaps.items.flatMap((g) => g.questions);
  check(name, gaps.totals.groups === gaps.items.length, "totals.groups");
  check(name, gaps.totals.not_found === questions.filter((q) => q.status === "not_found").length, "totals.not_found");
  check(name, gaps.totals.partial === questions.filter((q) => q.status === "partial").length, "totals.partial");
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(`mocks match ../backend/openapi.json: ${askMocks.length} answers, ${Object.keys(models).length} others`);
