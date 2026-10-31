package de.robv.android.xposed;

import java.lang.reflect.Member;

/** 编译期桩（真实实现由 LSPosed 提供）。 */
public abstract class XC_MethodHook {

    public static class MethodHookParam {
        public Member method;
        public Object thisObject;
        public Object[] args;
        private Object result;
        private Throwable throwable;

        public Object getResult() { return result; }
        public void setResult(Object r) { this.result = r; }
        public Throwable getThrowable() { return throwable; }
        public void setThrowable(Throwable t) { this.throwable = t; }
        public boolean hasThrowable() { return throwable != null; }
        public Object getResultOrThrowable() throws Throwable {
            if (throwable != null) throw throwable;
            return result;
        }
    }

    public class Unhook {
        public void unhook() { }
    }

    protected void beforeHookedMethod(MethodHookParam param) throws Throwable { }

    protected void afterHookedMethod(MethodHookParam param) throws Throwable { }
}
