import os
import uvicorn

if __name__ == "__main__":
    for d in ["data", "account", "gaps", "delete", "downloads", "static/css", "static/js", "templates"]:
        os.makedirs(d, exist_ok=True)

    port = int(os.getenv("PORT", 8080))
    
    print("=" * 50)
    print("  Telegram Adder Panel - WebUI")
    print(f"  Starting on port {port}")
    print("=" * 50)
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
        workers=1,
    )
