import contextlib,io,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from basis.progress import stage
from basis.execution import CommandRunner
from basis.cli import parser

class ProgressTests(unittest.TestCase):
    def test_heartbeat_cleanup_and_failure(self):
        buf=io.StringIO()
        with patch.dict(os.environ,{'BASIS_VERBOSE':'1'}),contextlib.redirect_stderr(buf):
            with self.assertRaises(ValueError):
                with stage('test',interval=.005):
                    time.sleep(.025);raise ValueError('expected')
            before=buf.getvalue();time.sleep(.015)
            self.assertEqual(before,buf.getvalue())
        self.assertIn('still running',before);self.assertIn('stopped with error',before)
    def test_quiet(self):
        buf=io.StringIO()
        with patch.dict(os.environ,{'BASIS_VERBOSE':'0'}),contextlib.redirect_stderr(buf):
            with stage('test'):pass
        self.assertEqual(buf.getvalue(),'')
    def test_native_stdout_not_contaminated(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'BASIS_VERBOSE':'1'}),contextlib.redirect_stderr(io.StringIO()):
            p=Path(d);r=CommandRunner({},p/'log')
            r.run([sys.executable,'-c','print("native-output")'],stdout_path=p/'out')
            self.assertEqual((p/'out').read_text(),'native-output\n')
    def test_cli(self):
        args=parser().parse_args(['run','-c','config.yaml','-d','-v']);self.assertTrue(args.verbose)
if __name__=='__main__':unittest.main()
