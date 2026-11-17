// AC922 ppc64le: vendored ggml ships no arch/powerpc/quants.c. Forward the non-generic
// dispatch symbols that ggml-cpu.c references to the reference *_generic impls (CPU is
// generic; MoE experts run on the GPU via native_experts, so this path is not hot).
#define GGML_COMMON_IMPL_C
#include "ggml-common.h"
#include "ggml-quants.h"
#include "ggml-impl.h"
#include "../../quants.h"

void ggml_vec_dot_q4_0_q8_0(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_q4_0_q8_0_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_q4_1_q8_1(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_q4_1_q8_1_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_q5_0_q8_0(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_q5_0_q8_0_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_q5_1_q8_1(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_q5_1_q8_1_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_q8_0_q8_0(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_q8_0_q8_0_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_q2_K_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_q2_K_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_q3_K_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_q3_K_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_q4_K_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_q4_K_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_q5_K_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_q5_K_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_q6_K_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_q6_K_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_iq2_xxs_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_iq2_xxs_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_iq2_xs_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_iq2_xs_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_iq3_xxs_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_iq3_xxs_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_iq1_s_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_iq1_s_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_iq4_nl_q8_0(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_iq4_nl_q8_0_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_iq3_s_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_iq3_s_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_iq2_s_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_iq2_s_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_iq4_xs_q8_K(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_iq4_xs_q8_K_generic(n, s, bs, vx, bx, vy, by, nrc); }
void ggml_vec_dot_mxfp4_q8_0(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) { ggml_vec_dot_mxfp4_q8_0_generic(n, s, bs, vx, bx, vy, by, nrc); }
void quantize_row_q8_0(const float * x, void * y, int64_t k) { quantize_row_q8_0_generic(x, y, k); }
void quantize_row_q8_1(const float * x, void * y, int64_t k) { quantize_row_q8_1_generic(x, y, k); }
