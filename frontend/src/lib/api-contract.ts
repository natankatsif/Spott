// The frontend's API types (./api.ts) checked against the backend's contract, ../../../backend/openapi.json, which
// `npm run api:types` turns into ./api-schema.d.ts. Types only, nothing runs: `next build` (tsc) fails if they drift.
// Responses: whatever the backend may send must fit the frontend's type. Requests: whatever the frontend sends must
// be accepted by the backend.

import type * as api from "./api";
import type { components } from "./api-schema";

type S = components["schemas"];

// FastAPI serializes every field of a response model, defaults included: in a response nothing is missing.
type Sent<T> = T extends (infer U)[] ? Sent<U>[] : T extends object ? { [K in keyof T]-?: Sent<T[K]> } : T;
type Fits<A, B> = [A] extends [B] ? true : false;
type Check<T extends { [K in keyof T]: true }> = T;
// The backend types a few maps as dict[str, …]; the frontend names their keys. Compared with string keys.
type AnyKeys<T> = { [key: string]: T[keyof T] };
type LLMSettingsMap = Omit<api.LLMSettings, "roles"> & { roles: AnyKeys<api.LLMSettings["roles"]> };
type PricingMap = Omit<api.Pricing, "rates"> & { rates: AnyKeys<api.Pricing["rates"]> };
type UsageReportMap = Omit<api.UsageReport, "pricing"> & { pricing: PricingMap };

export type ResponsesFit = Check<{
  AskResponse: Fits<Sent<S["AskResponse"]>, api.AskResponse>;
  SuggestionList: Fits<Sent<S["SuggestionList"]>, api.SuggestionList>;
  AdminSession: Fits<Sent<S["AdminSession"]>, api.AdminSession>;
  Job: Fits<Sent<S["Job"]>, api.Job>;
  SourceList: Fits<Sent<S["SourceList"]>, api.SourceList>;
  SourceAdded: Fits<Sent<S["SourceAdded"]>, api.SourceAdded>;
  GapList: Fits<Sent<S["GapList"]>, api.GapList>;
  GapRecheck: Fits<Sent<S["GapRecheck"]>, api.GapRecheck>;
  FeedbackItem: Fits<Sent<S["FeedbackItem"]>, api.FeedbackItem>;
  FeedbackStats: Fits<Sent<S["FeedbackStats"]>, api.FeedbackStats>;
  ApiError: Fits<Sent<S["ApiError"]>, api.ApiError>;
  HealthResponse: Fits<Sent<S["HealthResponse"]>, api.HealthResponse>;
  VisitorCount: Fits<Sent<S["VisitorCount"]>, api.VisitorCount>;
  LLMSettings: Fits<Sent<S["LLMSettingsView"]>, LLMSettingsMap>;
  LLMModelTest: Fits<Sent<S["ModelTest"]>, api.LLMModelTest>;
  Pricing: Fits<Sent<S["Pricing"]>, PricingMap>;
  UsageReport: Fits<Sent<S["UsageReport"]>, UsageReportMap>;
}>;

export type RequestsAccepted = Check<{
  AskRequest: Fits<api.AskRequest, S["AskRequest"]>;
  FeedbackRequest: Fits<api.FeedbackRequest, S["FeedbackRequest"]>;
  AdminLogin: Fits<api.AdminLogin, S["AdminLogin"]>;
  SourceCreate: Fits<api.SourceCreate, S["SourceCreate"]>;
  LLMSettingsUpdate: Fits<api.LLMSettingsUpdate, S["LLMSettingsUpdate"]>;
  LLMCheck: Fits<api.LLMCheck, S["ProviderCheck"]>;
}>;
