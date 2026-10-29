"""Runs inside Studio. `studio` is provided: studio.route(), studio.load()/save(), studio.log(), studio.data_dir."""
import time


@studio.route("greet")
def greet(body):
    state = studio.load({"visits": 0})
    state["visits"] += 1
    studio.save(state)
    studio.log("greeted", state["visits"])
    return {"message": f"Hello {body.get('name') or 'there'}!", "visits": state["visits"], "time": time.strftime("%H:%M:%S")}
