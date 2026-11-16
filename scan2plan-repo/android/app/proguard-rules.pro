# 默认不混淆；如需开启 minify，ARCore 需要保留其 native 绑定入口。
-keep class com.google.ar.core.** { *; }
-dontwarn com.google.ar.core.**
