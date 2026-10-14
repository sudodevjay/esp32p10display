"""Drive a running (or closed) Altium Designer through DelphiScript.

Each call writes a tiny script project (.PrjScr + .pas) into a work folder and starts
  X2.exe -RScriptingSystem:RunScript(ProjectName=...|ProcName=<file>.pas><proc>)
which works whether Altium is open or not. The script appends lines to a log file; we wait for a done marker.

Gotchas learned the hard way:
- Altium keeps an opened .pas in its editor and ignores later edits on disk -> every run uses a fresh file name.
- 'And' does not short-circuit in DelphiScript; 'Near' is reserved; a global TStringList is not Nil at start.
- If a script halts, Altium waits in the debugger and later RunScript calls queue up -> we time out and report.
"""
import os
import subprocess
import time
import uuid

from .project import find_x2

WORK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work")

PRELUDE = r"""
Const LOGF = '{log}';
Procedure L(S : String);
Var Lg : TStringList;
Begin
  Lg := TStringList.Create;
  If FileExists(LOGF) Then Lg.LoadFromFile(LOGF);
  Lg.Add(S); Lg.SaveToFile(LOGF); Lg.Free;
End;
Procedure OpenDoc(F, Kind : String);
Begin
  ResetParameters; AddStringParameter('ObjectKind', Kind); AddStringParameter('FileName', F);
  RunProcess('WorkspaceManager:OpenObject');
End;
Procedure CloseDoc(F : String);
Begin
  ResetParameters; AddStringParameter('ObjectKind', 'Document'); AddStringParameter('FileName', F);
  RunProcess('WorkspaceManager:CloseObject');
End;
"""


def run_script(body, proc="RunAll", timeout=300, done="@@DONE"):
    """body: DelphiScript source (procedures) that must call L('@@DONE') at the end of `proc`.
    Returns the log lines written by this run. Raises TimeoutError if the marker never appears."""
    os.makedirs(WORK, exist_ok=True)
    tag = "s" + uuid.uuid4().hex[:8]
    log = os.path.join(WORK, tag + ".log")
    pas = os.path.join(WORK, tag + ".pas")
    prj = os.path.join(WORK, tag + ".PrjScr")
    open(pas, "w", encoding="latin1").write(PRELUDE.replace("{log}", log) + body)
    open(prj, "w").write("[Design]\nVersion=1.0\nHierarchyMode=0\n\n[Document1]\nDocumentPath=%s.pas\n" % tag)
    # pass one raw command string: with an argument list Python would escape the inner quotes as \" and Altium
    # would not parse the RunScript parameters
    subprocess.Popen('"%s" -RScriptingSystem:RunScript(ProjectName="%s"|ProcName="%s.pas>%s")' % (find_x2(), prj, tag, proc))
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(2)
        if os.path.exists(log):
            lines = open(log, encoding="latin1").read().splitlines()
            if any(done in l or l.startswith("!!") for l in lines):
                return lines
    raise TimeoutError("Altium script %s did not finish in %ds (is Altium stuck in the script debugger?)" % (tag, timeout))


def generate_outputs(prj, outjob, timeout=600):
    """Run every output of an OutJob (Gerber/NC drill in 'Fabrication', DRC/ERC in 'Reports')."""
    body = r"""
Procedure RunAll;
Begin
  L('gen start');
  CloseDoc('%(oj)s');
  OpenDoc('%(prj)s', 'Project'); OpenDoc('%(oj)s', 'Document');
  ResetParameters;
  AddStringParameter('Action', 'Run'); AddStringParameter('ObjectKind', 'OutputBatch');
  AddStringParameter('OutputMedium', 'Fabrication'); AddStringParameter('PromptOverwrite', 'FALSE');
  RunProcess('WorkspaceManager:GenerateReport');
  L('fabrication done');
  ResetParameters;
  AddStringParameter('Action', 'Run'); AddStringParameter('ObjectKind', 'OutputBatch');
  AddStringParameter('OutputMedium', 'Reports'); AddStringParameter('PromptOverwrite', 'FALSE');
  RunProcess('WorkspaceManager:GenerateReport');
  L('@@DONE');
End;
""" % dict(prj=prj, oj=outjob)
    return run_script(body, timeout=timeout)
