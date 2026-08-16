### Fixed — `recall()` no longer issues three queries per candidate (`MEM-RECALL-N1-1`, #458)

Scoring ran `_get_model_by_id` (1 × `memory_nodes`) **plus** `get_graph_connectivity_score`
(2 × `memory_links` COUNT) **per candidate**, over up to `limit * 3` semantic *and* `limit * 3`
tag candidates.

- The re-fetch existed only to read four columns — `success_count`, `failure_count`,
  `usage_count`, `weight` — that the originating SELECT had already read and `_node_to_dict`
  then dropped. They are now carried, and the re-fetch is gone.
- Connectivity is now **two grouped queries for the whole candidate set** instead of two per
  candidate, via `get_graph_connectivity_scores()`. The per-node function is unchanged and
  still used elsewhere.

**Response shape:** `weight` is newly present on memory dicts.
`success_count` / `failure_count` / `usage_count` are **not** newly exposed — the scoring loop
already wrote them onto returned candidates — but they are now present *consistently*, including
when the old re-fetch would have missed (a row deleted between the two queries silently left the
counts unset).

Ranking is unchanged; equivalence with the per-node score is asserted, and a test counts real
SQL so a refactor that merely *looks* batched fails.
