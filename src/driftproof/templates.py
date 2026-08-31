from __future__ import annotations

CONTEXT_TEMPLATE = """# DriftProof Business Context

## Public contract

The public contract must expose `customer_id`, `full_name`, `net_amount`, `status`, `local_date`, and `net_revenue`.

## Required identifier

`customer_id` is required.

## Derived field

`full_name` is the trimmed concatenation of `first_name` and `last_name`.

## Numeric failure policy

Invalid numeric values in `net_amount` must become null and use decimal conversion.

## Latest-record policy

The grain is one row per `customer_id`; select the row with greatest `updated_at`.

## Categorical mapping

Map `pending -> open` and `paid -> closed`.

## Timezone policy

Source timestamps are UTC and must be converted to America/New_York before casting to date.

## Formula

`net_revenue = sales - refunds`.

## Macro contract

The macro uses keyword argument `scale` with scale=2.
"""
