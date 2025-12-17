import yaml
from typing import Dict, Any

class PromptManager:
    def __init__(self, prompt_path: str = "prompts"):
        self.prompt_path = prompt_path
        self._cache = {}

    def load_prompt(self, name: str) -> Dict[str, Any]:
        """
        load the desired prompt from the prompt path
        :param name: logical prompt name, e.g.: "report"
        :return: a dictionary of loaded prompt with metadata
        """
        file_path = f"./{self.prompt_path}/{name}.yaml"
        if file_path in self._cache:
            return self._cache[file_path]
        with open(file_path, "r", encoding="utf-8") as f:
            prompt_object = yaml.safe_load(f)
        self._cache[file_path] = prompt_object
        print(f"[PROMPT][{name}] version={prompt_object.get('version')}")
        return prompt_object

    def render_prompt(self, name:str, **kwargs) -> Dict[str, Any]:
        """
        Render the prompt by filling in variables. Return each piece of rendered contents (system, task, rules, etc.) with inserted variables.
        :param name: logical prompt name, e.g.: "report"
        :param kwargs: additional variables to fill in the prompt
        :return: a dictionary of rendered prompt components
        """
        prompt_object = self.load_prompt(name)
        rendered = {}
        # current keys: system, task, rule, inputs
        for key in ("system", "task", "rules", "inputs"):
            if key in prompt_object:
                s = prompt_object[key]
                for var, val in kwargs.items():
                    s = s.replace(f"{{{{{var}}}}}", str(val))
                rendered[key] = s
        return rendered

    def construct_prompt(self, name:str, **kwargs) -> str:
        """
        construct the prompt from the rendered blocks
        :param name: logical prompt name, e.g.: "report"
        :param kwargs: additional variables to fill in the prompt
        :return: structured prompt to be used by llm
        """
        blocks = self.render_prompt(name, **kwargs)
        constructed_prompt = ""
        if "system" in blocks:
            constructed_prompt += f"[System]\n{blocks['system']}\n\n"
        if "task" in blocks:
            constructed_prompt += f"[Task]\n{blocks['task']}\n\n"
        if "rules" in blocks:
            constructed_prompt += f"[Rules]\n{blocks['rules']}\n\n"
        if "inputs" in blocks:
            constructed_prompt += f"[Inputs]\n{blocks['inputs']}\n"
        return constructed_prompt.strip()
