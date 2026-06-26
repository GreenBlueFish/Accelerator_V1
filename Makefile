# Makefile for SNN accelerator cocotb testbench
#
# Usage:
#   make                        - run all tests
#   make TEST=test_bram_readwrite  - run a single test by name
#   make WAVES=1                - dump VCD waveforms
#   make SIMULATOR=verilator    - use Verilator (default: icarus)
#
# Prerequisites:
#   pip install cocotb cocotb-bus
#   sudo apt install iverilog        (Icarus Verilog)
#   or: sudo apt install verilator   (Verilator, faster)

TOPLEVEL_LANG   ?= verilog
SIMULATOR       ?= icarus
TOPLEVEL        ?= accelerator
MODULE          ?= tests.test_accelerator

# --------------------------------------------------------------------------
# Source files — adjust paths if running from a different directory
# --------------------------------------------------------------------------
VERILOG_SOURCES  = $(PWD)/rtl/accelerator.v
VERILOG_SOURCES += $(PWD)/rtl/engine_cover.v
VERILOG_SOURCES += $(PWD)/rtl/engine.v
VERILOG_SOURCES += $(PWD)/rtl/Gate_2SEL15.v
VERILOG_SOURCES += $(PWD)/rtl/ramb36e1.v

# --------------------------------------------------------------------------
# Accelerator parameters — must match test_accelerator.py constants
# --------------------------------------------------------------------------
COMPILE_ARGS += -P accelerator.D_WIDTH_LOG=15
COMPILE_ARGS += -P accelerator.F_WIDTH_LOG=1
COMPILE_ARGS += -P accelerator.DEPTH=1024
COMPILE_ARGS += -P accelerator.ADDR_BITS=10
COMPILE_ARGS += -P accelerator.NEURONS=2

# --------------------------------------------------------------------------
# Optional: VCD waveform dump (set WAVES=1 on command line)
# --------------------------------------------------------------------------
ifeq ($(WAVES), 1)
    COMPILE_ARGS  += -g2012
    SIM_ARGS      += --vcd=waveform.vcd
    export COCOTB_RESOLVE_X ?= ZEROS
endif

# --------------------------------------------------------------------------
# Icarus-specific: support SystemVerilog-style generate blocks
# --------------------------------------------------------------------------
ifeq ($(SIMULATOR), icarus)
    COMPILE_ARGS += -g2012
endif

# --------------------------------------------------------------------------
# Run a single test (e.g. make TEST=test_bram_readwrite)
# --------------------------------------------------------------------------
ifdef TEST
    COCOTB_TESTCASE ?= $(TEST)
    export COCOTB_TESTCASE
endif

# --------------------------------------------------------------------------
# Cocotb include — must come last
# --------------------------------------------------------------------------
include $(shell cocotb-config --makefiles)/Makefile.sim
