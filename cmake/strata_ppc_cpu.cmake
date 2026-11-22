# cmake/strata_ppc_cpu.cmake - POWER CPU generation flags for the whole build.
#
#   -DSTRATA_PPC_CPU=auto|power8|power9|none      (default: auto)
#
# Why this exists: distro toolchains bake a baseline into the compiler.  On RHEL's gcc-toolset for ppc64le that
# baseline is -mcpu=power8 -mtune=power8, so a POWER9 machine compiles at the POWER8 ISA floor and loses the
# POWER9 codegen paths.  The deep-window hot loops here are host-CPU expert math (VSX/altivec dot products),
# the single largest non-GPU cost per decode window, so running them at the machine's real generation is a
# pure codegen gain: no numerics change, no instruction beyond the hardware (MMA and other POWER10 additions
# are never emitted by -mcpu=power9, and this module never raises the request above what /proc/cpuinfo proves).
#
# auto reads this machine's generation (boot CPU's pvr: PowerPC97/98 = POWER9, PowerPC440..220/450d = POWER8)
# and applies only the delta against the compiler's current default: if the compiler already defaults to the
# machine's generation, nothing is added.  Every flag is probed with check_cxx_compiler_flag before use.
#
# Only ppc64le is touched.  On every other processor this module is a no-op (matching the STRATA_ISA_FLOOR
# x86-only convention).  CUDA device code is unaffected: the flags apply to the host compilation only.
#
# Included by CMakeLists.txt before any target is declared (add_compile_options is positional).

set(STRATA_PPC_CPU "auto" CACHE STRING "POWER cpu generation flags: auto, power8, power9, none")
set_property(CACHE STRATA_PPC_CPU PROPERTY STRINGS auto power8 power9 none)

if(NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(ppc64le|powerpc64le|ppc64)$")
  if(NOT STRATA_PPC_CPU STREQUAL "auto")
    message(WARNING "Strata: STRATA_PPC_CPU=${STRATA_PPC_CPU} is a POWER build option and does not apply to ${CMAKE_SYSTEM_PROCESSOR}; ignored")
    set(STRATA_PPC_CPU "auto" CACHE STRING "" FORCE)
  endif()
  return()
endif()

include(CheckCXXCompilerFlag)

if(STRATA_PPC_CPU STREQUAL "none")
  message(STATUS "Strata: POWER cpu flags: none (STRATA_PPC_CPU=none, the compiler's own default stands)")
  return()
endif()

function(_strata_ppc_machine_gen _out)
  set(_gen "")
  if(EXISTS /proc/cpuinfo)
    file(READ /proc/cpuinfo _ci)
    # channel 1: the cpu line, "cpu : POWER9, altivec supported" (ppc64le Linux) or "cpu : PowerPC97 ..." (legacy)
    if(_ci MATCHES "cpu[ \t]*:[ \t]*POWER([0-9]+)")
      if(CMAKE_MATCH_1 STREQUAL "9")
        set(_gen power9)
      elseif(CMAKE_MATCH_1 STREQUAL "8")
        set(_gen power8)
      endif()
    elseif(_ci MATCHES "cpu[ \t]*:[ \t]*PowerPC([0-9A-Fa-f]+)")
      set(_pvrhex "${CMAKE_MATCH_1}")
      string(TOUPPER "${_pvrhex}" _pvrhex)
      if(_pvrhex MATCHES "^(0*4[DdEe]|0*97|0*98)$")
        set(_gen power9)
      elseif(_pvrhex MATCHES "^(0*4[BCbc]|0*2[02]0|0*440|0*450[Dd])$")
        set(_gen power8)
      endif()
    endif()
    # channel 2: revision pvr, "revision : 2.1 (pvr 004e 1201)"
    if(NOT _gen AND _ci MATCHES "pvr[ \t]+([0-9a-fA-F]+)")
      set(_pvrhex "${CMAKE_MATCH_1}")
      string(TOUPPER "${_pvrhex}" _pvrhex)
      if(_pvrhex MATCHES "^(004D|004E)$")
        set(_gen power9)
      elseif(_pvrhex MATCHES "^(004B|004C)$")
        set(_gen power8)
      endif()
    endif()
  endif()
  set(${_out} "${_gen}" PARENT_SCOPE)
endfunction()

# what does the compiler already default to?  (empty = the bare target default, which on distro gcc-toolset
# builds is a baked -mcpu; the probe below compares against that by checking whether the request differs)
_strata_ppc_machine_gen(_strata_ppc_machine)

set(_strata_ppc_want "")
if(STRATA_PPC_CPU STREQUAL "auto")
  if(NOT _strata_ppc_machine)
    message(STATUS "Strata: POWER cpu flags: auto found no recognised POWER generation in /proc/cpuinfo; compiler default stands")
    return()
  endif()
  set(_strata_ppc_want "${_strata_ppc_machine}")
else()
  set(_strata_ppc_want "${STRATA_PPC_CPU}")
  if(_strata_ppc_machine AND NOT _strata_ppc_machine STREQUAL _strata_ppc_want)
    message(WARNING "Strata: STRATA_PPC_CPU=${_strata_ppc_want} but this machine reports ${_strata_ppc_machine}: the binary may not run here")
  endif()
endif()

# does the compiler's default target already carry the wanted generation?  (gcc-toolset's baked -mcpu
# differs per distro; probe the arch macro with no flags of ours rather than assume)
if(_strata_ppc_want STREQUAL "power9")
  set(_strata_ppc_macro "_ARCH_PWR9")
else()
  set(_strata_ppc_macro "_ARCH_PWR8")
endif()
include(CheckCXXSourceCompiles)
check_cxx_source_compiles("#ifndef ${_strata_ppc_macro}\n#error not this generation\n#endif\nint main(){return 0;}" STRATA_PPC_DEFAULT_GEN)
if(STRATA_PPC_DEFAULT_GEN)
  message(STATUS "Strata: POWER cpu flags: compiler default already targets ${_strata_ppc_want}; nothing added")
  return()
endif()

set(_strata_ppc_flags "")
foreach(_f "-mcpu=${_strata_ppc_want}" "-mtune=${_strata_ppc_want}")
  string(MAKE_C_IDENTIFIER "STRATA_HAVE_${_f}" _var)
  set(CMAKE_REQUIRED_FLAGS "${_f}")
  check_cxx_compiler_flag("${_f}" ${_var})
  set(CMAKE_REQUIRED_FLAGS "")
  if(${_var})
    list(APPEND _strata_ppc_flags "${_f}")
  else()
    message(WARNING "Strata: this compiler rejects ${_f}; the generation build needs gcc 5.2+ / clang 4+ on POWER")
  endif()
endforeach()

if(NOT _strata_ppc_flags)
  return()
endif()

foreach(_f ${_strata_ppc_flags})
  add_compile_options($<$<COMPILE_LANGUAGE:CXX>:${_f}>)
  add_compile_options($<$<COMPILE_LANGUAGE:C>:${_f}>)
  # host flags for nvcc, so the C++ standard-library and our headers compile the same on both sides of the split
  add_compile_options($<$<COMPILE_LANGUAGE:CUDA>:-Xcompiler=${_f}>)
endforeach()
message(STATUS "Strata: POWER cpu flags: ${_strata_ppc_flags} (machine: ${_strata_ppc_machine}, request: ${STRATA_PPC_CPU})")
