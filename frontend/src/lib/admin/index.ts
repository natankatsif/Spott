// The admin panel's data layer (docs/API.md → "Admin"): the session (./session.ts), every /api/admin/* call
// (./client.ts; in mock mode the demo backend, ./demo.ts), and the hook that loads them into a page (./query.ts).

export { admin, isActive } from "./client";
export { type Query, useAdminQuery } from "./query";
export { signIn, signOut, takeExpiredFlag, useAdminSession, useHydrated } from "./session";
