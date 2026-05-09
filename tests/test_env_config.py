import importlib
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class EnvConfigTests(unittest.TestCase):
    def test_utils_reads_azure_endpoint_deployment_and_version_from_env(self):
        old_env = {
            "AZURE_OPENAI_API_KEY": os.environ.get("AZURE_OPENAI_API_KEY"),
            "AZURE_OPENAI_KEY": os.environ.get("AZURE_OPENAI_KEY"),
            "AZURE_OPENAI_ENDPOINT": os.environ.get("AZURE_OPENAI_ENDPOINT"),
            "AZURE_OPENAI_DEPLOYMENT": os.environ.get("AZURE_OPENAI_DEPLOYMENT"),
            "AZURE_OPENAI_API_VERSION": os.environ.get("AZURE_OPENAI_API_VERSION"),
        }
        os.environ["AZURE_OPENAI_KEY"] = "k-test"
        os.environ["AZURE_OPENAI_ENDPOINT"] = "https://example.openai.azure.com/openai/"
        os.environ["AZURE_OPENAI_DEPLOYMENT"] = "dep-test"
        os.environ["AZURE_OPENAI_API_VERSION"] = "2099-01-01-preview"
        try:
            import utils
            utils = importlib.reload(utils)
            self.assertEqual(utils.AZURE_API_KEY, "k-test")
            self.assertEqual(utils.AZURE_ENDPOINT, "https://example.openai.azure.com")
            self.assertEqual(utils.DEPLOYMENT_NAME, "dep-test")
            self.assertEqual(utils.API_VERSION, "2099-01-01-preview")
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if "utils" in sys.modules:
                importlib.reload(sys.modules["utils"])


if __name__ == "__main__":
    unittest.main()
