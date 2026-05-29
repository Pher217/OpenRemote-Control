def handle(thread, args):
    if not args:
        return {"ok": False, "message": "Usage: /model <name>"}
    thread.metadata["model"] = args[0]
    thread.save(update_fields=["metadata"])
    return {"ok": True, "message": f"Model set to {args[0]}."}
