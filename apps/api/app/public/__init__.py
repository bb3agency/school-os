"""Public surface: handles that lead to one school without a signed-in member (R2; ADR-0047,
ADR-0046). Parent link tokens, certificate codes and provider webhook routes resolve to a school
through ``core.resolve_public_handle`` only; every later read runs in that school's
``tenant_session`` under RLS. Other modules call :mod:`app.public.service` only."""
