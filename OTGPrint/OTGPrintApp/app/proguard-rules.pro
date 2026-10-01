# JNI 保持: PrintEngine.nativeEncode 不被裁剪
-keepclasseswithmembernames class com.otgprint.app.PrintEngine {
    native <methods>;
}
