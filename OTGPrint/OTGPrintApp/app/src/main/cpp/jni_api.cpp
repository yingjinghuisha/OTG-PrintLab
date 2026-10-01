/**
 * jni_api.cpp — Kotlin <-> C++ JNI 桥
 *
 * 提供:
 *   nativeEncode(rgba, w, h, dpi, paper) -> ByteArray 完整PCL3-GUI打印流 (单页)
 *   多页流式: nativeBeginJob() / nativeEncodePage(...) / nativeEndJob()
 *             拼接后为完整多页打印流 (文档打印: PDF/Word)
 */
#include <jni.h>
#include <string>
#include "pcl3gui_encoder.h"

namespace {

jbyteArray toJavaBytes(JNIEnv* env, const std::vector<uint8_t>& v) {
    if (v.empty()) return nullptr;
    jbyteArray out = env->NewByteArray(static_cast<jsize>(v.size()));
    if (out == nullptr) return nullptr;
    env->SetByteArrayRegion(out, 0, static_cast<jsize>(v.size()),
                            reinterpret_cast<const jbyte*>(v.data()));
    return out;
}

} // namespace

extern "C" {

JNIEXPORT jbyteArray JNICALL
Java_com_otgprint_app_PrintEngine_nativeEncode(JNIEnv* env, jclass,
                                                jbyteArray rgba, jint srcW, jint srcH,
                                                jint dpi, jint paper) {
    if (rgba == nullptr || srcW <= 0 || srcH <= 0) return nullptr;

    jsize n = env->GetArrayLength(rgba);
    if (n != (jsize)srcW * srcH * 4) return nullptr;

    jboolean isCopy = JNI_FALSE;
    jbyte* px = env->GetByteArrayElements(rgba, &isCopy);
    if (px == nullptr) return nullptr;

    pcl3gui::PrintParams params;
    params.dpi = dpi;
    params.paper = static_cast<pcl3gui::PaperSize>(paper);

    std::vector<uint8_t> stream = pcl3gui::encodeImage(
        reinterpret_cast<const uint8_t*>(px), srcW, srcH, params);

    env->ReleaseByteArrayElements(rgba, px, JNI_ABORT);

    if (stream.empty()) return nullptr;
    return toJavaBytes(env, stream);
}

// ---- 多页流式接口 ----

// 任务头: reset + UEL + PJL (每任务一次)
JNIEXPORT jbyteArray JNICALL
Java_com_otgprint_app_PrintEngine_nativeBeginJob(JNIEnv* env, jclass) {
    return toJavaBytes(env, pcl3gui::buildJobPrologue());
}

// 单页: 页面设置 + 光栅 + *rC + 换页 (每页调用一次)
// srcDpi > 0: 文档实际大小模式 (位图按 srcDpi 渲染); 0: 照片适应模式
JNIEXPORT jbyteArray JNICALL
Java_com_otgprint_app_PrintEngine_nativeEncodePage(JNIEnv* env, jclass,
                                                   jbyteArray rgba, jint srcW, jint srcH,
                                                   jint dpi, jint paper, jint srcDpi) {
    if (rgba == nullptr || srcW <= 0 || srcH <= 0) return nullptr;

    jsize n = env->GetArrayLength(rgba);
    if (n != (jsize)srcW * srcH * 4) return nullptr;

    jboolean isCopy = JNI_FALSE;
    jbyte* px = env->GetByteArrayElements(rgba, &isCopy);
    if (px == nullptr) return nullptr;

    pcl3gui::PrintParams params;
    params.dpi = dpi;
    params.paper = static_cast<pcl3gui::PaperSize>(paper);
    params.srcDpi = srcDpi;

    std::vector<uint8_t> page = pcl3gui::encodePage(
        reinterpret_cast<const uint8_t*>(px), srcW, srcH, params);

    env->ReleaseByteArrayElements(rgba, px, JNI_ABORT);

    if (page.empty()) return nullptr;
    return toJavaBytes(env, page);
}

// 任务尾: reset + UEL + PJL EOJ (每任务一次)
JNIEXPORT jbyteArray JNICALL
Java_com_otgprint_app_PrintEngine_nativeEndJob(JNIEnv* env, jclass) {
    return toJavaBytes(env, pcl3gui::buildJobEpilogue());
}

} // extern "C"
