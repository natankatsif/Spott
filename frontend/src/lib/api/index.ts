// The frontend's side of the API. The contract is backend/openapi.json (generated from the backend's models): these
// types are checked against it in ../api-contract.ts, so the build fails if they drift. Human-readable spec: docs/API.md.
// One module per concern: types.ts (the contract), http.ts, client.ts (public endpoints), admin.ts, mocks.ts.

export * from "./admin";
export * from "./client";
export { API_URL, ApiRequestError, checked } from "./http";
export * from "./mocks";
export * from "./types";
export { isMock } from "../mode";
