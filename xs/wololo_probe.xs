// wololo_probe.xs — smoke test 0: does XS file I/O work in the Feral macOS port?
//
// Drop into the user XS folder, make a scenario whose Map > Script Filename
// is "wololo_probe", test it, then look for wololo_probe.xsdat (or
// default0.xsdat when testing from the editor).  See docs/de_bridge.md.

int probeTicks = 0;

void writeProbe() {
    probeTicks = probeTicks + 1;
    xsCreateFile(false);          // file is named after the scenario
    xsWriteInt(41186);            // MAGIC
    xsWriteInt(1);                // VERSION
    xsWriteInt(probeTicks);       // seq
    xsCloseFile();
    xsChatData("[wololo probe] wrote frame %d", probeTicks);
}

rule wololoProbe
    active
    minInterval 2
    maxInterval 2
{
    writeProbe();
}
