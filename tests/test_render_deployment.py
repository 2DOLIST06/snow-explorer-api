import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RenderDeploymentTests(unittest.TestCase):
    def test_blueprint_uses_docker_runtime_and_repository_dockerfile(self):
        blueprint = (ROOT / "render.yaml").read_text()
        self.assertIn("runtime: docker", blueprint)
        self.assertIn("dockerfilePath: ./Dockerfile", blueprint)
        self.assertIn("dockerContext: .", blueprint)

    def test_docker_build_installs_and_checks_poppler_and_python_dependencies(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("apt-get install -y --no-install-recommends poppler-utils", dockerfile)
        self.assertIn("RUN command -v pdfinfo && command -v pdftoppm", dockerfile)
        self.assertIn("RUN pip install --no-cache-dir -r requirements.txt", dockerfile)

    def test_start_command_checks_poppler_runs_gunicorn_and_uses_render_port(self):
        command = next(line[4:] for line in (ROOT / "Dockerfile").read_text().splitlines()
                       if line.startswith("CMD "))
        argv = json.loads(command)
        self.assertEqual(argv[:2], ["sh", "-c"])
        self.assertIn("command -v pdfinfo && command -v pdftoppm", argv[2])
        self.assertIn("exec gunicorn", argv[2])
        self.assertIn("0.0.0.0:${PORT:-5001}", argv[2])
        self.assertIn("app.main:app", argv[2])


if __name__ == "__main__":
    unittest.main()
