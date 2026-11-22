/**
 * Longest literal seed-example search string the API accepts; longer input is
 * truncated before the request is sent. Shared by the request builder in
 * `services/api.ts` and the query-string round trip in
 * `components/Datasets/seedExampleQuery.ts`.
 */
export const MAX_SEED_SEARCH_LENGTH = 1_000
