# Langfuse usage

Local Docker stack health:

```powershell
python langfuse_usage/check_docker.py
```

Aggregate token and cost usage by model for the last seven days:

```powershell
python langfuse_usage/token_usage.py --days 7
```

The script uses `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` from `.env`. It calls the Langfuse v4 Metrics API and never prints credentials.

If it reports authentication failure, the configured key pair belongs to a different Langfuse project or host. Create a project API key in the local Langfuse UI and update the two key values in `.env`.
