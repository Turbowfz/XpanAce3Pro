"""Module readiness/input tests; fake sources do not certify device permissions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ANDROID = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or
               Path.home() / "AppData/Local/Android/Sdk") / "platforms/android-35/android.jar"


class ModuleGateTests(unittest.TestCase):
    def test_java_gate_against_profile_receipts(self):
        with tempfile.TemporaryDirectory(prefix="module-gate-", dir=ROOT / "_tmp") as folder:
            folder = Path(folder)
            source = folder / "ModuleGateTest.java"
            source.write_text(HARNESS, encoding="utf-8")
            classes = folder / "classes"
            classes.mkdir()
            subprocess.run([shutil.which("javac"), "-encoding", "UTF-8", "-source", "8", "-target", "8",
                            "-bootclasspath", str(ANDROID), "-cp", str(ANDROID), "-d", str(classes),
                            str(ROOT / "hook/CameraProfile.java"), str(ROOT / "hook/ModuleGate.java"), str(source)],
                           check=True, cwd=ROOT)
            for generation in ("c16", "c17"):
                receipt = json.loads((ROOT / "_tmp/profile-build/profiles" / generation / "receipt.json").read_text(encoding="utf-8"))
                args = [generation, receipt["camera_version"], receipt["build_fingerprint"]]
                args.extend(receipt["files"][name]["output"]["sha256"] for name in
                            ("camera_unit_config", "camera_unit_feature_config.protobuf", "oplus_camera_config"))
                args.extend((receipt["sdk_sha256"], receipt["adapter_sha256"]))
                subprocess.run([shutil.which("java"), "-cp", os.pathsep.join((str(classes), str(ANDROID))),
                                "com.xpanport.hook.ModuleGateTest", *args], check=True, cwd=ROOT)


HARNESS = r'''package com.xpanport.hook;
import java.util.*;
public final class ModuleGateTest {
  static int checks;
  static void check(boolean ok, String why) { checks++; if (!ok) throw new AssertionError(why); }
  static final String ROOT = "/product/framework/";
  static final String SDK = "com.oplus.camera.unit.sdk.jar", ADAPTER = "com.oplus.camera.unit.sdk.adapter.jar";
  static class Source implements ModuleGate.Source {
    Map<String,String> props = new HashMap<String,String>(), hashes = new HashMap<String,String>();
    String model = "PJX110", fingerprint, version;
    String[] libs = {ROOT + SDK, ROOT + ADAPTER};
    long now; int reads, hashesRead, tearAt; boolean throwHash, hostileFailure;
    Source(String[] a) {
      version=a[1]; fingerprint=a[2];
      props.put("debug.xpan.active","1"); props.put("debug.xpan.profile",a[0]);
      props.put("debug.xpan.token","01234567-0123-0123-0123-0123456789ab");
      String[] paths={"/odm/etc/camera/config/camera_unit_config", "/odm/etc/camera/config/camera_unit_feature_config.protobuf",
          "/odm/etc/camera/config/oplus_camera_config", ROOT+SDK, ROOT+ADAPTER};
      for(int i=0;i<paths.length;i++) hashes.put(paths[i],a[i+3]);
    }
    public String property(String n) { reads++; if(reads==tearAt) return "torn"; return props.get(n); }
    public String model(){return model;} public String fingerprint(){return fingerprint;}
    public String cameraVersion(){return version;} public String[] libraries(){return libs;}
    public String hash(String path){
      hashesRead++;
      if(hostileFailure)throw new IllegalStateException(){ public String toString(){throw new AssertionError("hostile tostring");} };
      if(throwHash)throw new IllegalStateException("denied"); return hashes.get(path);
    }
    public long elapsed(){return now;}
    ModuleGate gate(){return new ModuleGate(this,CameraProfile.select("PJX110",version,fingerprint));}
  }
  public static void main(String[] a){
    Source s=new Source(a); ModuleGate g=s.gate();
    check(g.verified(),"qualified input refused"); check(s.hashesRead==5,"not all actual inputs checked");
    check(g.enabled() && s.hashesRead==5,"hot cache not used");
    check(g.verified() && s.hashesRead==10,"forced save qualification cached hashes");
    s.now=3000;check(g.enabled()&&s.hashesRead==15,"expired cache not checked");
    s.props.put("debug.xpan.active","0");check(!g.enabled(),"inactive cache accepted");
    s.props.put("debug.xpan.active","1");check(!g.verified(),"revoked process revived");
    s=new Source(a);g=s.gate();check(g.enabled(),"initial token refused");
    check(g.detail().contains("qualified"),"qualified detail absent");
    s.now=-1;check(g.enabled()&&s.hashesRead==10,"clock rollback bypassed fresh hashes");
    s.props.put("debug.xpan.token","11234567-0123-0123-0123-0123456789ab");check(!g.enabled(),"reinstall adopted new token");
    for(String key:new String[]{"debug.xpan.active","debug.xpan.profile","debug.xpan.token"}){
      s=new Source(a);s.props.remove(key);check(!s.gate().enabled(),"missing state accepted: "+key);
    }
    for(int i=1;i<=12;i++){s=new Source(a);s.tearAt=i;check(!s.gate().verified(),"torn state accepted "+i);}
    s=new Source(a);s.model="PLK110";check(!s.gate().enabled(),"wrong model accepted");
    s=new Source(a);s.fingerprint+="-ota";check(!s.gate().enabled(),"OTA accepted");
    s=new Source(a);g=s.gate();s.version+="-updated";check(!g.enabled(),"camera update accepted");
    s=new Source(a);s.throwHash=true;check(!s.gate().enabled(),"unreadable files accepted");
    s=new Source(a);s.hostileFailure=true;g=s.gate();check(!g.enabled(),"hostile exception escaped gate");
    s.hostileFailure=false;check(!g.enabled(),"hostile error failed to revoke process");
    for(int i=3;i<a.length;i++){
      String[] bad=a.clone();bad[i]="0000000000000000000000000000000000000000000000000000000000000000";
      s=new Source(bad);check(!s.gate().enabled(),"wrong hash accepted "+i);
    }
    s=new Source(a);g=s.gate();check(g.enabled(),"valid cache start");
    s.hashes.put(ROOT+SDK,"wrong");s.now=3000;check(!g.enabled(),"changed effective SDK accepted");
    for(String[] paths:new String[][]{null,{}, {ROOT+ADAPTER}, {ROOT+SDK,ROOT+SDK,ROOT+ADAPTER},
        {ROOT+SDK,"/data/adb/"+ADAPTER}, {ROOT+SDK,"/my_product/product_overlay/framework/"+ADAPTER},
        {"/product/framework/../framework/"+SDK,ROOT+ADAPTER}, {null,ROOT+SDK,ROOT+ADAPTER},
        {ROOT+SDK,ROOT+"com.oplus.camera.unit.sdk.jar.extra.jar"},
        {ROOT+SDK,"/system/framework/same.jar","/system/framework/same.jar"},
        {ROOT+SDK,"invalid-library-text"}, {ROOT+SDK,"/system//framework/other.jar"},
        {ROOT+SDK,"/system/framework/../other.jar"}})
      check(ModuleGate.libraryPaths(paths)==null,"unknown/shared library paths accepted");
    String[] tooMany = new String[65]; tooMany[0]=ROOT+SDK;
    for(int i=1;i<tooMany.length;i++) tooMany[i]="/system/framework/lib"+i+".jar";
    check(ModuleGate.libraryPaths(tooMany)==null,"unbounded declaration accepted");
    check(ModuleGate.libraryPaths(new String[]{ROOT+SDK,"libOpenCL.so","libOpenCL.so"})==null,
        "duplicate unrelated native declarations accepted");
    check(ModuleGate.libraryPaths(new String[]{ROOT+SDK,"libcdsprpc.so","libOpenCL.so"})!=null,
        "real unrelated native library declarations refused");
    check(Arrays.equals(ModuleGate.libraryPaths(new String[]{ROOT+SDK}), new String[]{ROOT+SDK,ROOT+ADAPTER}),
        "unit-only declaration did not check same-parent adapter footprint");
    s=new Source(a);s.libs=new String[]{ROOT+SDK};check(s.gate().verified(),"actual unit-only app declaration refused");
    s=new Source(a);s.libs=new String[]{ROOT+SDK};s.hashes.put(ROOT+ADAPTER,"wrong");
    check(!s.gate().verified(),"missing direct adapter skipped pinned footprint hash");
    s=new Source(a);s.libs=new String[]{ROOT+SDK};s.hashes.remove(ROOT+ADAPTER);
    check(!s.gate().verified(),"missing adapter footprint silently accepted");
    check(ModuleGate.libraryPaths(new String[]{ROOT+SDK,ROOT+ADAPTER,"/system/framework/other.jar"})!=null,"unrelated lib rejected");
    check(ModuleGate.libraryPaths(new String[]{"/my_product/product_overlay/framework/"+SDK,
        "/my_product/product_overlay/framework/"+ADAPTER})!=null,"real overlay parent rejected");
    System.out.println("PASS "+a[0]+" ModuleGate "+checks+" checks; offline state/hash tests only");
  }
}'''

if __name__ == "__main__":
    unittest.main(verbosity=2)
