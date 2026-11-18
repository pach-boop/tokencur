# 0020. RunPod billing from API v2: no usage time, and storage of its own

- Status: Accepted
- Date: 2026-11-18

## Context

ADR 0009 brought RunPod's bill into tokencur through REST API v1,
`GET https://rest.runpod.io/v1/billing/pods`: one row per pod and day with
the dollars, the GPU time billed and the disk GB billed. Between 14 and 17
November 2026 that host stopped resolving, and RunPod's documentation now
says REST API v1 "is deprecated and no longer available" (#49).

API v2 (`https://api.runpod.io/v2`, OpenAPI 2.0.0) has two endpoints for
what tokencur needs:

- `GET /v2/billing/pods`: one record per pod and bucket, with the bucket's
  start and end and the dollars, split into GPU, CPU and disk. It gives
  no GPU time and no disk size.
- `GET /v2/billing/network-volumes`: one record per network volume and
  bucket, with the dollars, split into standard and high-performance
  storage. v1 never listed this charge. A network volume is billed while
  it exists, with or without a pod.

A real capture on 2026-11-18 matched every pod and day the saved v1
captures had: 98 charges, the same day buckets (UTC midnight) and the same
amounts to the last digit. v2 also listed 14 pod charges from 6 to 10
October ($3.01) that no v1 capture had, and 44 days of a network volume
($1.97, about $0.05 a day).

Three things needed a decision: the quantity of a charge that comes with
no usage, what happens to the GPU time v1 measured when v2 bills the same
charge again, and where the storage goes.

## Decision

- **v2 charges are stated over their bucket, in hours.** FOCUS wants a
  quantity on every usage row, and ADR 0018 already settled the case of a
  bill without one: the quantity is the period's length. A pod's day is
  24 hours. The detail says so and gives the split
  (`GPU $0.4000, disk $0.0200; no usage time in RunPod's API v2: stated
  over the period`).
- **A charge says whether its quantity was stated.** `BilledCharge` gains
  `quantity_stated`, and the ledger (schema 10) keeps it. The Anthropic
  and OpenAI lines ADR 0018 stated over their period set it too, and the
  migration marks the stored ones by the note their detail carries.
- **Measured usage is not replaced by stated usage.** A pod's charge for a
  day has the same id in both APIs (`<podId>@<bucket start>`), because it
  is the same charge. When a later bill restates a charge, the money comes
  from the later bill. Its usage (quantity, unit and detail) replaces the
  stored usage, unless the stored usage was measured and the new one is
  only stated. So a v2 capture over the year keeps the GPU time v1
  measured, and the order in which captures are imported does not matter.
- **Network volumes are their own service.** They become
  `RunPod Network Volumes` charges of resource type `network volume`, and
  FOCUS `Storage` rows in the `File Storage` subcategory, with their own
  SKU. Reports list them next to `RunPod Pods`.
- **The fetch script saves one file per request.** It calls both endpoints
  and writes each response in the same wrapper as before, so each file
  still names the one request that produced it.
  `tokencur import runpod` takes both files at once.
- **Saved v1 captures still import.** The importer tells the two shapes
  apart: a v1 response is a list of rows, and a v2 response is an object
  with `records`.

## Consequences

- RunPod's billed total in the ledger is complete again: pods from both
  APIs and the storage. On the maintainer's ledger it rose from $27.52 to
  $32.50.
- From 14 November 2026 on, pod charges carry no GPU time. A unit price
  per GPU-hour is only right for the charges v1 measured. For the later
  ones, the FOCUS unit price is per hour of the bucket.
- A charge that was only ever stated keeps the stated quantity. If RunPod
  adds usage back to v2, a measured quantity replaces it without a change
  to the ledger.
- The disk GB v1 gave for a stopped pod stays on those charges. v2 gives
  only the disk's dollars, in the detail.
