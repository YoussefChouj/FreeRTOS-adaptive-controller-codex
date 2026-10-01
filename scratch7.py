import re

with open("ground_station/service/campaign_api.py", "r") as f:
    content = f.read()

old_state_return = """            return {
                "status": st,
                "waiting_pack": self.waiting_pack,
                "campaign_path": self.campaign_path if (self.runner_thread and self.runner_thread.is_alive()) or self.report else None,
                "reason": self.report.reason if self.report else "",
                "flights": flights,
                "control": self.control.get() if self.runner_thread and self.runner_thread.is_alive() else None
            }"""

new_state_return = """            return {
                "status": st,
                "waiting_pack": self.waiting_pack,
                "campaign_path": self.campaign_path if (self.runner_thread and self.runner_thread.is_alive()) or self.report else None,
                "reason": self.report.reason if self.report else "",
                "flights": flights,
                "control": self.control.get() if self.runner_thread and self.runner_thread.is_alive() else None,
                "arm_refusal": self.arm_refusal
            }"""
            
content = content.replace(old_state_return, new_state_return)

with open("ground_station/service/campaign_api.py", "w") as f:
    f.write(content)
