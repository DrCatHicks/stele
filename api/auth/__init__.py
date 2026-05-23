"""Operator authentication and the application-role model (design doc §3.10).

Application roles {admin, researcher, reviewer} are an authorization concept
layered on the single least-privileged ``stele_api`` connection — they do NOT
map to the Postgres roles of §3.3. Sessions are server-side and revocable,
carried in a signed httpOnly cookie. RBAC enforcement (M3.2) builds on the
``current_user`` dependency defined here.
"""
