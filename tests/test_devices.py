import copy
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from basis.config import default_config, new_study_config
from basis.devices import configure_devices, verify_inference_device


class DevicePolicyTests(unittest.TestCase):
    def gpu_probe(self, stdout="6144\n"):
        return subprocess.CompletedProcess(["nvidia-smi"], 0, stdout, "")

    def test_auto_driver_detection_reserves_matching_gpu_budget_without_mutating_config(self):
        cfg = new_study_config()
        before = copy.deepcopy(cfg)
        with patch("basis.devices.shutil.which", return_value="/usr/bin/nvidia-smi"), \
                patch("basis.devices.subprocess.run", return_value=self.gpu_probe()) as probe:
            selected = configure_devices(cfg, extend=True)
        self.assertEqual(cfg, before)
        self.assertEqual(selected["execution"]["gpus"], 1)
        self.assertEqual(selected["modules"]["pathogenfinder2"]["gpus"], 1)
        self.assertFalse(selected["execution"]["device_resolution"]["torch_runtime_verified"])
        self.assertEqual(probe.call_args.kwargs["timeout"], 10)

    def test_auto_driver_missing_small_or_invalid_falls_back_to_cpu(self):
        for output in ("2048\n", "", "unavailable\n"):
            with self.subTest(output=output), \
                    patch("basis.devices.shutil.which", return_value="/usr/bin/nvidia-smi"), \
                    patch("basis.devices.subprocess.run", return_value=self.gpu_probe(output)):
                selected = configure_devices(new_study_config(), extend=True)
                self.assertEqual(selected["execution"]["gpus"], 0)
                self.assertEqual(selected["modules"]["pathogenfinder2"]["gpus"], 0)
        with patch("basis.devices.shutil.which", return_value=None), \
                patch("basis.devices.subprocess.run") as probe:
            selected = configure_devices(new_study_config(), extend=True)
            probe.assert_not_called()
            self.assertFalse(selected["execution"]["device_resolution"]["gpu_reserved"])

    def test_auto_driver_probe_failure_or_timeout_falls_back(self):
        errors = (OSError("driver missing"), subprocess.TimeoutExpired("nvidia-smi", 10),
                  subprocess.CalledProcessError(1, "nvidia-smi"))
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                    patch("basis.devices.shutil.which", return_value="/usr/bin/nvidia-smi"), \
                    patch("basis.devices.subprocess.run", side_effect=error):
                selected = configure_devices(new_study_config(), extend=True)
                self.assertEqual(selected["execution"]["gpus"], 0)

    def test_explicit_cuda_does_not_silently_fall_back(self):
        cfg = new_study_config()
        cfg["execution"]["device"] = "cuda"
        with patch("basis.devices.shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "CUDA explicitly requested"):
                configure_devices(cfg, extend=True)

    def test_explicit_cpu_avoids_driver_probe(self):
        cfg = new_study_config()
        cfg["execution"].update(device="cpu", gpus=1)
        cfg["modules"]["pathogenfinder2"]["gpus"] = 1
        with patch("basis.devices.subprocess.run") as probe:
            selected = configure_devices(cfg, extend=True)
        probe.assert_not_called()
        self.assertEqual(selected["execution"]["gpus"], 0)
        self.assertEqual(selected["modules"]["pathogenfinder2"]["gpus"], 0)

    def test_legacy_or_inactive_extend_configuration_unchanged(self):
        scenarios = [(default_config(), True), (new_study_config(), False)]
        disabled = new_study_config()
        disabled["modules"]["pathogenfinder2"]["enabled"] = False
        scenarios.append((disabled, True))
        imported = new_study_config()
        imported["modules"]["pathogenfinder2"].update(mode="import", result="saved.json")
        scenarios.append((imported, True))
        for cfg, extend in scenarios:
            before = copy.deepcopy(cfg)
            with self.subTest(extend=extend, module=cfg["modules"]["pathogenfinder2"]), \
                    patch("basis.devices.shutil.which") as which, \
                    patch("basis.devices.subprocess.run") as probe:
                self.assertEqual(configure_devices(cfg, extend), before)
                which.assert_not_called()
                probe.assert_not_called()

    def runner(self, available, gpus=1):
        runner = Mock()
        runner.gpus = gpus
        runner.run.return_value = subprocess.CompletedProcess(["python"], 0,
            json.dumps({"cuda_available": available, "torch_version": "fixture-version"}), "")
        return runner

    def test_runtime_auto_gpu_unavailable_falls_back_and_updates_runner(self):
        runner = self.runner(False)
        cfg = {"device_policy": "auto", "gpus": 1}
        result = verify_inference_device(cfg, runner)
        self.assertEqual(result["selected"], "cpu")
        self.assertEqual(cfg["gpus"], 0)
        self.assertEqual(runner.gpus, 0)

    def test_runtime_cuda_must_be_available_and_reserved(self):
        for available, reserved in ((False, 1), (True, 0)):
            with self.subTest(available=available, reserved=reserved):
                runner = self.runner(available, reserved)
                with self.assertRaisesRegex(ValueError, "CUDA requested but unavailable"):
                    verify_inference_device({"device_policy": "cuda", "gpus": reserved}, runner)

    def test_runtime_verified_gpu_and_cpu_selection(self):
        runner = self.runner(True)
        cfg = {"device_policy": "auto", "gpus": 1, "python_executable": "python3"}
        self.assertEqual(verify_inference_device(cfg, runner)["selected"], "cuda")
        self.assertEqual(runner.run.call_args.args[0][0], "python3")
        cpu = {"device_policy": "cpu", "gpus": 0}
        self.assertEqual(verify_inference_device(cpu, runner)["selected"], "cpu")
        self.assertEqual(runner.gpus, 0)

    def test_runtime_legacy_does_not_probe_or_change_runner(self):
        runner = self.runner(True)
        self.assertIsNone(verify_inference_device({"gpus": 1}, runner))
        runner.run.assert_not_called()
        self.assertEqual(runner.gpus, 1)

    def test_runtime_invalid_probe_output_and_broken_torch_fail(self):
        runner = self.runner(False)
        runner.run.return_value.stdout = "not JSON"
        with self.assertRaises(ValueError):
            verify_inference_device({"device_policy": "auto", "gpus": 1}, runner)
        runner.run.side_effect = RuntimeError("Torch import failed")
        with self.assertRaisesRegex(RuntimeError, "Torch import failed"):
            verify_inference_device({"device_policy": "auto", "gpus": 1}, runner)


if __name__ == "__main__":
    unittest.main()
