"""FastAPI web sim: mouse is the ferret, prey is the Zaber carriage."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from prey_gantry.engine import HuntSim

STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
	app = FastAPI(title="Prey gantry hunt sim")
	sim = HuntSim()
	app.state.sim = sim
	app.mount("/static", StaticFiles(directory=STATIC), name="static")

	@app.get("/")
	async def index() -> FileResponse:
		return FileResponse(STATIC / "index.html")

	@app.websocket("/ws")
	async def ws_endpoint(ws: WebSocket) -> None:
		await ws.accept()
		task = asyncio.create_task(_sim_loop(ws, sim))
		try:
			while True:
				msg = await ws.receive_json()
				_handle_client(sim, msg)
		except WebSocketDisconnect:
			pass
		finally:
			task.cancel()

	return app


def _handle_client(sim: HuntSim, msg: dict) -> None:
	kind = msg.get("type")
	if kind == "pointer":
		sim.set_pointer(float(msg["x_mm"]), float(msg["y_mm"]))
	elif kind == "trial":
		sim.set_trial(str(msg.get("cmd", "")))


async def _sim_loop(ws: WebSocket, sim: HuntSim) -> None:
	# Why: 1 ms physics keeps 200 fps grabs and 4 ms Zaber RTT honest vs wall clock.
	step_s = 0.001
	send_every = 0.033
	acc = 0.0
	last = time.perf_counter()
	last_send = last
	while True:
		await asyncio.sleep(0.0)
		now = time.perf_counter()
		acc += min(now - last, 0.05)
		last = now
		while acc >= step_s:
			sim.step(step_s)
			acc -= step_s
		if now - last_send >= send_every:
			last_send = now
			await ws.send_json(sim.snapshot())


def main() -> None:
	import uvicorn

	uvicorn.run(
		"prey_gantry.web.app:create_app",
		factory=True,
		host="0.0.0.0",
		port=8765,
		reload=False,
	)


if __name__ == "__main__":
	main()
