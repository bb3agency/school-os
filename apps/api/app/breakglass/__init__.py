"""Break-glass support access, school side (US-103, FR-OPS-004, SEC-021; docs/07 §6.4).

The control plane raises a request (reason, scope, duration <= 8 h); this module pulls it into
the school's ``ops.break_glass_grants``, lets an owner/principal approve (step-up) or deny it,
gives the operator a temporary read-only ``platform_support`` membership on approval, lets the
school revoke it at any time and ends it automatically when the window passes. Every step is
audited in the school's chain and reported back to the control-plane chain.
"""
