"""Shared Supabase client.

The service-role key bypasses row level security, so this client must never be
reachable without passing through the admin login in `blueprints/auth.py`.
"""

from supabase import Client, create_client

from config import Config

Config.validate()

supabase: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SERVICE_KEY)
