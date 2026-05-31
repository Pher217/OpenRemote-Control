def handle(thread, args):
    if not args:
        return {"ok": False, "message": "Usage: /account <label>"}
    thread.metadata["account"] = args[0]
    thread.save(update_fields=["metadata"])
    return {"ok": True, "message": f"Account set to {args[0]}."}
