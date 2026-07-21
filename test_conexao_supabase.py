import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"]
)

print("✅ Conectado ao Supabase!\n")

# Storage
print("Buckets:")

buckets = supabase.storage.list_buckets()

for bucket in buckets:
    print(f" - {bucket.name}")

# Tabelas
print("\nTabelas:")

tables = [
    "pdfs",
    "article_metadata",
    "page_images",
    "page_blocks",
    "profiles"
]

for table in tables:
    try:
        supabase.table(table).select("id").limit(1).execute()
        print(f" - {table} ✅")
    except Exception:
        print(f" - {table} ❌ Não encontrada")