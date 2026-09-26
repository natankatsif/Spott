"use client";

// One assistant answer, composed only from AI Elements (+ their shadcn primitives).
// Data comes from viewOf(message) — see lib/chat-transport.ts.

import {
  AlertTriangleIcon,
  BookOpenIcon,
  CheckIcon,
  CopyIcon,
  FileSearchIcon,
  ListTreeIcon,
  SearchIcon,
  ShieldCheckIcon,
  TextSearchIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react";
import { Fragment, useState } from "react";
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
import type { Citation, TraceStep } from "@/lib/api";
import { sendFeedback } from "@/lib/api";
import type { AnswerView } from "@/lib/chat-transport";
import type { UIText } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const TOOL_ICONS: Record<TraceStep["tool"], typeof SearchIcon> = {
  search: SearchIcon,
  grep: TextSearchIcon,
  toc: ListTreeIcon,
  open: BookOpenIcon,
  verify: ShieldCheckIcon,
};

export function citationLabel(c: Citation, t: UIText): string {
  return [c.document_title, c.location, c.page ? `${t.page} ${c.page}` : null].filter(Boolean).join(", ");
}

function CitationCard({ citations, t }: { citations: Citation[]; t: UIText }) {
  if (!citations.length) return null;
  return (
    <InlineCitation>
      <InlineCitationCard>
        <InlineCitationCardTrigger sources={citations.map((c) => c.url)} />
        <InlineCitationCardBody>
          <InlineCitationCarousel>
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
                  <a className="text-brand text-xs underline underline-offset-2" href={c.deep_link} rel="noreferrer" target="_blank">
                    {t.openSource} ↗
                  </a>
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
    : a.status === "partial" ? t.partialNote
    : a.status === "conflict" ? (a.conflict?.kind === "outdated" ? t.outdatedTitle : t.conflictTitle)
    : null;
  if (!note) return null;
  const warn = a.status === "conflict" || a.status === "partial";
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
  const [copied, setCopied] = useState(false);
  const byId = new Map(view.citations.map((c) => [c.id, c]));
  const a = view.answer;
  const text = view.sentences.map((s) => s.text).join(" ");

  const rate = (v: "up" | "down") => {
    if (!a || vote) return;
    setVote(v);
    void sendFeedback({ answer_id: a.id, vote: v });
  };

  return (
    <Message from="assistant">
      <MessageContent className="w-full gap-4">
        {view.trace.length > 0 && (
          <ChainOfThought defaultOpen={false}>
            <ChainOfThoughtHeader>{t.howSearched(view.trace.length)}</ChainOfThoughtHeader>
            <ChainOfThoughtContent>
              {view.trace.map((step, i) => (
                <ChainOfThoughtStep
                  description={step.input}
                  icon={TOOL_ICONS[step.tool] ?? FileSearchIcon}
                  key={`${step.tool}-${i}`}
                  label={`${t.tools[step.tool]} · ${step.summary}`}
                  status={streaming && i === view.trace.length - 1 && !view.sentences.length ? "active" : "complete"}
                />
              ))}
            </ChainOfThoughtContent>
          </ChainOfThought>
        )}

        {streaming && view.sentences.length === 0 && <Shimmer>{t.searching}</Shimmer>}

        <StatusNote t={t} view={view} />

        {view.sentences.length > 0 && (
          <p className="text-[15px] leading-7">
            {view.sentences.map((s) => (
              <Fragment key={s.index}>
                <span className={cn(s.done && !s.verified && s.cites.length > 0 && "decoration-warning/60 decoration-dotted underline")}>
                  {s.text}
                </span>
                <CitationCard citations={s.cites.flatMap((id) => byId.get(id) ?? [])} t={t} />{" "}
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
                  <CitationCard citations={step.cites.flatMap((id) => byId.get(id) ?? [])} t={t} />
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

        {view.citations.length > 0 && (
          <Sources>
            <SourcesTrigger className="text-muted-foreground text-xs" count={view.citations.length}>
              <BookOpenIcon className="size-3.5" />
              <span>{t.sourcesUsed(view.citations.length)}</span>
            </SourcesTrigger>
            <SourcesContent>
              {view.citations.map((c) => (
                <Source href={c.deep_link} key={c.id} title={citationLabel(c, t)} />
              ))}
            </SourcesContent>
          </Sources>
        )}

        {a && a.nav_links.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <span className="text-muted-foreground">{t.whereToGo}:</span>
            {a.nav_links.map((l) => (
              <a className="text-brand underline underline-offset-2" href={l.url} key={l.url} rel="noreferrer" target="_blank">
                {l.title}
              </a>
            ))}
          </div>
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
            <MessageAction disabled={!!vote} label={t.helpful} onClick={() => rate("up")} tooltip={t.helpful}>
              <ThumbsUpIcon className={cn("size-3.5", vote === "up" && "fill-current")} />
            </MessageAction>
            <MessageAction disabled={!!vote} label={t.notHelpful} onClick={() => rate("down")} tooltip={t.notHelpful}>
              <ThumbsDownIcon className={cn("size-3.5", vote === "down" && "fill-current")} />
            </MessageAction>
            {vote && <span className="text-muted-foreground text-xs">{t.thanks}</span>}
            {a.meta.verified && a.citations.length > 0 && (
              <span className="ml-2 flex items-center gap-1 text-success text-xs">
                <ShieldCheckIcon className="size-3.5" /> {t.verified}
              </span>
            )}
          </MessageActions>
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
