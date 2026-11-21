import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("ucuzgez.app:app", host=os.environ.get("UG_HOST", "0.0.0.0"),
                port=int(os.environ.get("UG_PORT", "8000")), workers=1)  # tek worker: zamanlayıcı tek kopya çalışsın
