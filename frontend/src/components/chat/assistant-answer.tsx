"use client";

// One assistant answer, composed only from AI Elements (+ their shadcn primitives).
// Data comes from viewOf(message) — see lib/chat-transport.ts.

import {
  AlertTriangleIcon,
  BookOpenIcon,
  ScanSearchIcon,
  CheckIcon,
  ClockIcon,
  CopyIcon,
  FileSearchIcon,
  ListTreeIcon,
  MailIcon,
  MapPinIcon,
  PhoneIcon,
  SearchIcon,
  ShieldCheckIcon,
  TextSearchIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react";
import { Fragment, useRef, useState } from "react";
import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import {
  InlineCitation,
  InlineCitationCard,
  InlineCitationCardBody,
  InlineCitationCardTrigger,
  InlineCitationCarousel,
  InlineCitationCarouselContent,
  InlineCitationCarouselHeader,
  InlineCitationCarouselIndex,
  InlineCitationCarouselItem,
  InlineCitationCarouselNext,
  InlineCitationCarouselPrev,
  InlineCitationQuote,
  InlineCitationSource,
} from "@/components/ai-elements/inline-citation";
import { Message, MessageAction, MessageActions, MessageContent } from "@/components/ai-elements/message";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Source, Sources, SourcesContent, SourcesTrigger } from "@/components/ai-elements/sources";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import { Task, TaskContent, TaskItem, TaskTrigger } from "@/components/ai-elements/task";
import { CitationPager } from "@/components/chat/citation-pager";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { canPreview, LocateBadge, useSourcePreview } from "@/components/chat/source-preview";
import type { Citation, ContactCard, FeedbackTag, TraceStep } from "@/lib/api";
import { sendFeedback } from "@/lib/api";
import type { AnswerView } from "@/lib/chat-transport";
import type { UIText } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const DISLIKE_REASONS = ["wrong", "outdated", "incomplete", "wrong_source", "not_understood"] as const satisfies FeedbackTag[];

const TOOL_ICONS: Record<TraceStep["tool"], typeof SearchIcon> = {
  search: SearchIcon,
  grep: TextSearchIcon,
  toc: ListTreeIcon,
  open: BookOpenIcon,
  verify: ShieldCheckIcon,
};

/** Sentence index → the citations its badge shows. A run of sentences citing the same documents gets one badge,
 * after its last sentence, with the quotes of the whole run. */
function badges(sentences: AnswerView["sentences"], byId: Map<string, Citation>): Map<number, Citation[]> {
  const docsOf = (cites: string[]) => [...new Set(cites.flatMap((id) => byId.get(id)?.doc_id ?? []))].sort().join("|");
  const out = new Map<number, Citation[]>();
  let run: Citation[] = [];
  sentences.forEach((s, i) => {
    run = [...run, ...s.cites.flatMap((id) => byId.get(id) ?? []).filter((c) => !run.includes(c))];
    const next = sentences[i + 1];
    if (!next || !s.cites.length || docsOf(next.cites) !== docsOf(s.cites)) {
      if (run.length) out.set(i, run);
      run = [];
    }
  });
  return out;
}

export function citationLabel(c: Citation, t: UIText): string {
  return [c.document_title, c.location, c.page ? `${t.page} ${c.page}` : null].filter(Boolean).join(", ");
}

function CitationCard({ citations, all, t }: { citations: Citation[]; all: Citation[]; t: UIText }) {
  const preview = useSourcePreview();
  // Radix HoverCard ignores touch, so on phones the badge opens (and closes) on tap; a mouse still uses hover.
  // A tap outside the card closes it (the card's dismissable layer).
  const [open, setOpen] = useState(false);
  const pointer = useRef<string>("mouse");
  if (!citations.length) return null;
  return (
    <InlineCitation>
      <InlineCitationCard onOpenChange={setOpen} open={open}>
        <InlineCitationCardTrigger
          className="cursor-pointer select-none"
          onClick={() => pointer.current !== "mouse" && setOpen((o) => !o)}
          onPointerDown={(e) => {
            pointer.current = e.pointerType;
          }}
          sources={citations.map((c) => c.url)}
        />
        <InlineCitationCardBody>
          {/* phones swipe between sources; a mouse only uses the arrows, so dragging selects the quote's text */}
          <InlineCitationCarousel opts={{ watchDrag: (_, evt) => !(evt instanceof MouseEvent) }}>
            {citations.length > 1 && (
              <InlineCitationCarouselHeader>
                <InlineCitationCarouselPrev />
                <InlineCitationCarouselNext />
                <InlineCitationCarouselIndex />
              </InlineCitationCarouselHeader>
            )}
            <InlineCitationCarouselContent>
              {citations.map((c) => (
                <InlineCitationCarouselItem key={c.id}>
                  <InlineCitationSource title={citationLabel(c, t)} url={c.site ?? c.url} />
                  <InlineCitationQuote>{c.quote}</InlineCitationQuote>
                  {c.translation && (
                    <p className="text-muted-foreground text-xs">
                      <span className="font-medium">{t.aiTranslation}:</span> {c.translation}
                    </p>
                  )}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    {/* no line to look for → nothing to show in the document: only the original link */}
                    {canPreview(c) && (
                      <button
                        className="inline-flex items-center gap-1 font-medium text-brand text-xs hover:underline"
                        onClick={() => preview.open(all, c.id)}
                        type="button"
                      >
                        <ScanSearchIcon className="size-3.5" /> {t.preview.show}
                      </button>
                    )}
                    <a className="text-muted-foreground text-xs underline underline-offset-2" href={c.deep_link} rel="noreferrer" target="_blank">
                      {t.openSource} ↗
                    </a>
                  </div>
                </InlineCitationCarouselItem>
              ))}
            </InlineCitationCarouselContent>
          </InlineCitationCarousel>
        </InlineCitationCardBody>
      </InlineCitationCard>
    </InlineCitation>
  );
}

function StatusNote({ view, t }: { view: AnswerView; t: UIText }) {
  const a = view.answer;
  if (!a) return null;
  const note =
    a.status === "not_found" ? t.notFoundTitle
    : a.status === "refused" ? t.refusedTitle
    : a.status === "conflict" ? (a.conflict?.kind === "outdated" ? t.outdatedTitle : t.conflictTitle)
    : null;
  if (!note) return null;
  const warn = a.status === "conflict";
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border px-3 py-2 text-sm",
        warn ? "border-warning/30 bg-warning-bg text-warning" : "border-border bg-muted text-muted-foreground",
      )}
    >
      <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
      <div>
        <p className="font-medium">{note}</p>
        {a.conflict && <p className="mt-1 text-foreground/80">{a.conflict.explanation}</p>}
      </div>
    </div>
  );
}

// "Where to go": phones to call, e-mails, the address on Google Maps, the hours; the name links to where it is written.
function ContactBlock({ contact: c }: { contact: ContactCard }) {
  const inCity = c.address && /chi[șs]in[ăa]u|кишин/i.test(c.address) ? c.address : `${c.address}, Chișinău`;
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border px-3 py-2">
      <a className="font-medium hover:underline" href={c.deep_link} rel="noreferrer" target="_blank">
        {c.name}
      </a>
      {c.phone.map((p) => (
        <a className="flex items-center gap-2 text-brand" href={`tel:${p.replace(/[^\d+]/g, "")}`} key={p}>
          <PhoneIcon className="size-3.5 shrink-0" />
          {p}
        </a>
      ))}
      {c.email.map((e) => (
        <a className="flex items-center gap-2 text-brand" href={`mailto:${e}`} key={e}>
          <MailIcon className="size-3.5 shrink-0" />
          {e}
        </a>
      ))}
      {c.address && (
        <a
          className="flex items-center gap-2 text-brand"
          href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(inCity)}`}
          rel="noreferrer"
          target="_blank"
        >
          <MapPinIcon className="size-3.5 shrink-0" />
          {c.address}
        </a>
      )}
      {c.hours && (
        <span className="flex items-center gap-2 text-muted-foreground">
          <ClockIcon className="size-3.5 shrink-0" />
          {c.hours}
        </span>
      )}
    </div>
  );
}

export function AssistantAnswer({
  view,
  streaming,
  t,
  onFollowup,
}: {
  view: AnswerView;
  streaming: boolean;
  t: UIText;
  onFollowup: (q: string) => void;
}) {
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const [reasons, setReasons] = useState<FeedbackTag[]>([]);
  const preview = useSourcePreview();
  const shown = preview.state?.citations[preview.state.index];
  const activeId = shown && view.citations.includes(shown) ? shown.id : undefined;
  const [copied, setCopied] = useState(false);
  const byId = new Map(view.citations.map((c) => [c.id, c]));
  const a = view.answer;
  const text = view.sentences.map((s) => s.text).join(" ");
  const badgeAt = badges(view.sentences, byId);
  // how it searched: under the finished answer, and only when the documents were involved (sources cited, or
  // searched and not found); a greeting or an off-topic question shows none
  const showSearch =
    !streaming && view.trace.length > 0 && (view.citations.length > 0 || a?.status === "not_found" || a?.status === "partial");
  const noDocs = !!a && a.citations.length === 0 && a.trace.length === 0; // a greeting, off-topic, a clarifying question

  const rate = (v: "up" | "down") => {
    if (!a || vote) return;
    setVote(v);
    void sendFeedback({ answer_id: a.id, vote: v });
  };

  return (
    <Message from="assistant">
      <MessageContent className="w-full gap-4">
        {streaming && view.sentences.length === 0 && <Shimmer>{t.searching}</Shimmer>}

        <StatusNote t={t} view={view} />

        {view.sentences.length > 0 && (
          <p className="text-[15px] leading-7">
            {view.sentences.map((s, i) => (
              <Fragment key={s.index}>
                <span className={cn(s.done && !s.verified && s.cites.length > 0 && "decoration-warning/60 decoration-dotted underline")}>
                  {s.text}
                </span>
                {/* sentences in a row from the same documents: one badge after the last of them, with all their quotes */}
                {badgeAt.has(i) && <CitationCard all={view.citations} citations={badgeAt.get(i) ?? []} t={t} />}{" "}
              </Fragment>
            ))}
          </p>
        )}

        {a?.checklist && (
          <Task>
            <TaskTrigger title={a.checklist.title} />
            <TaskContent>
              {a.checklist.steps.map((step, i) => (
                <TaskItem key={i}>
                  <span className="font-medium text-foreground">{i + 1}.</span> {step.text}
                  <CitationCard all={view.citations} citations={step.cites.flatMap((id) => byId.get(id) ?? [])} t={t} />
                </TaskItem>
              ))}
              {a.checklist.documents_needed.length > 0 && (
                <TaskItem>
                  <span className="font-medium text-foreground">{t.checklistDocs}:</span> {a.checklist.documents_needed.join(", ")}
                </TaskItem>
              )}
              {a.checklist.fee && <TaskItem>{t.fee}: {a.checklist.fee}</TaskItem>}
              {a.checklist.deadline && <TaskItem>{t.deadline}: {a.checklist.deadline}</TaskItem>}
            </TaskContent>
          </Task>
        )}

        {!streaming && view.citations.length > 0 && (
          <Sources className="hidden lg:block">
            <SourcesTrigger className="text-muted-foreground text-xs" count={view.citations.length}>
              <BookOpenIcon className="size-3.5" />
              <span>{t.sourcesUsed(view.citations.length)}</span>
            </SourcesTrigger>
            <SourcesContent>
              {view.citations.map((c) => (
                <div className="flex flex-wrap items-center gap-x-2" key={c.id}>
                  <Source
                    className={cn("flex items-center gap-2 rounded-md", activeId === c.id && "text-brand")}
                    href={c.deep_link}
                    onClick={(e) => {
                      // open our preview; ctrl/cmd-click (or a citation without a line to find) opens the original
                      if (!canPreview(c) || e.metaKey || e.ctrlKey || e.shiftKey) return;
                      e.preventDefault();
                      preview.open(view.citations, c.id);
                    }}
                    title={citationLabel(c, t)}
                  />
                  {activeId === c.id && <LocateBadge className="pl-6" status={preview.status} t={t} />}
                </div>
              ))}
            </SourcesContent>
          </Sources>
        )}

        {/* phones: one citation at a time (swipe or ← →) instead of a list */}
        {!streaming && view.citations.length > 0 && <CitationPager citations={view.citations} className="lg:hidden" t={t} />}

        {a && a.contacts.length > 0 && (
          <div className="flex flex-col gap-2 text-sm">
            <span className="text-muted-foreground">{t.whereToGo}:</span>
            {a.contacts.map((c) => (
              <ContactBlock contact={c} key={`${c.name}-${c.url}`} />
            ))}
          </div>
        )}

        {showSearch && (
          <ChainOfThought defaultOpen={false}>
            <ChainOfThoughtHeader>{t.howSearched(view.trace.length)}</ChainOfThoughtHeader>
            <ChainOfThoughtContent>
              {a?.status === "partial" && (
                <ChainOfThoughtStep icon={AlertTriangleIcon} label={t.partialNote} status="complete" />
              )}
              {view.trace.map((step, i) => (
                <ChainOfThoughtStep
                  description={step.input}
                  icon={TOOL_ICONS[step.tool] ?? FileSearchIcon}
                  key={`${step.tool}-${i}`}
                  label={`${t.tools[step.tool]} · ${step.summary}`}
                  status="complete"
                />
              ))}
            </ChainOfThoughtContent>
          </ChainOfThought>
        )}
      </MessageContent>

      {a && !streaming && (
        <>
          <MessageActions>
            <MessageAction
              label={t.copy}
              onClick={() => {
                void navigator.clipboard.writeText(text);
                setCopied(true);
              }}
              tooltip={t.copy}
            >
              {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
            </MessageAction>
            {!noDocs && (
              <>
                <MessageAction disabled={!!vote} label={t.helpful} onClick={() => rate("up")} tooltip={t.helpful}>
                  <ThumbsUpIcon className={cn("size-3.5", vote === "up" && "fill-current")} />
                </MessageAction>
                <MessageAction disabled={!!vote} label={t.notHelpful} onClick={() => rate("down")} tooltip={t.notHelpful}>
                  <ThumbsDownIcon className={cn("size-3.5", vote === "down" && "fill-current")} />
                </MessageAction>
                {vote && <span className="text-muted-foreground text-xs">{t.thanks}</span>}
              </>
            )}
            {a.meta.verified && a.citations.length > 0 && (
              <span className="ml-2 flex items-center gap-1 text-success text-xs">
                <ShieldCheckIcon className="size-3.5" /> {t.verified}
              </span>
            )}
          </MessageActions>
          {/* after a dislike: what was wrong, one tap each; sent as the same rating, so it replaces it */}
          {vote === "down" && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-muted-foreground">{t.whatWrong}</span>
              <ToggleGroup
                className="flex-wrap"
                onValueChange={(v: string[]) => {
                  const tags = v as FeedbackTag[];
                  setReasons(tags);
                  if (a) void sendFeedback({ answer_id: a.id, vote: "down", tags });
                }}
                size="sm"
                spacing={1}
                type="multiple"
                value={reasons}
                variant="outline"
              >
                {DISLIKE_REASONS.map((r) => (
                  <ToggleGroupItem
                    className="h-7 !rounded-full px-3 text-xs data-[state=on]:border-foreground data-[state=on]:bg-foreground data-[state=on]:text-background"
                    key={r}
                    value={r}
                  >
                    {t.reasons[r]}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </div>
          )}
          {a.followups.length > 0 && (
            <Suggestions>
              {a.followups.map((q) => (
                <Suggestion key={q} onClick={onFollowup} suggestion={q} />
              ))}
            </Suggestions>
          )}
        </>
      )}
    </Message>
  );
}
