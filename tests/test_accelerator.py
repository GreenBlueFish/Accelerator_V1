"""
test_accelerator.py
===================
Cocotb testbench for accelerator.v (SNN accelerator)

Hardware summary
----------------
accelerator
├── TRACES          ramb36e1  – bram_output[0]  (trace / IO / E+I neuron data)
└── core[0..N-1]
    ├── LOGIC       engine_cover
    │   └── engine  (MAC-based LIF compute unit)
    └── WEIGHTS     ramb36e1  – bram_output[i+1]

engine_cover register map (reg_sel)
-------------------------------------
  0x0  mode[1:0], w_sel (bit 2)
  0x1  x_post   [DATA+FIRING:0]
  0x2  x_pre    [DATA+FIRING:0]
  0x3  neg_x_threshold [DATA:0]
  0x4  x_reset  [DATA:0]
  0x5  w_syn    [DATA:0]
  0x6  a_neg    [DATA:0]
  0x7  a_pos    [DATA:0]
  0x8  dr       [DATA:0]

engine modes
------------
  00  Update x_post (LIF synaptic integration)
  01  Threshold / Reset
  10  Weight update (STDP)
  11  Forward time step (weight decay)

Default parameters (match accelerator.v defaults)
  D_WIDTH_LOG  = 15   (DATA = 14 inside engine_cover/engine)
  F_WIDTH_LOG  = 1    (FIRING = 1)
  D_WIDTH_RAM  = 16   (D_WIDTH_LOG + F_WIDTH_LOG)
  DEPTH        = 1024
  ADDR_BITS    = 10
  NEURONS      = 2

Fixed-point format
  Q1.14  (1 sign bit, 14 fractional bits, i.e. scale = 2^14 = 16384)
  Firing bits sit above the data field in x_post / x_pre
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, Timer
from cocotb.types import LogicArray
import math
import sys
from enum import Enum
import csv

# ---------------------------------------------------------------------------
# Parameters – must match the RTL parameters you are simulating with
# ---------------------------------------------------------------------------

D_WIDTH_LOG  = 15      # logic data width (total data bits in engine_cover)
F_WIDTH_LOG  = 1       # firing bits
D_WIDTH_RAM  = D_WIDTH_LOG + F_WIDTH_LOG   # = 16
NEURONS      = 2
DEPTH        = 1024
ADDR_BITS    = 10
SQRT_NEURONS = math.ceil(math.log2(NEURONS + 1))   # $clog2(NEURONS+1)

# Convenience
DATA         = D_WIDTH_LOG - 1   # = 14  (matches engine_cover DATA param)
FIRING       = F_WIDTH_LOG       # = 1
TOTAL_BITS   = DATA + FIRING + 1  # = D_WIDTH_RAM = 16

# Fixed-point scale factor Q1.14
FP_SCALE     = 1 << DATA         # 16384

# engine_cover reg_sel addresses
REG_MODE_WSEL        = 0x0
REG_X_POST           = 0x1
REG_X_PRE            = 0x2
REG_NEG_X_THRESHOLD  = 0x3
REG_X_RESET          = 0x4
REG_W_SYN            = 0x5
REG_A_NEG            = 0x6
REG_A_POS            = 0x7
REG_DR               = 0x8

# bram_in_select values
BRAM_SRC_DATA_IN     = 0   # external data_in bus
BRAM_SRC_LOGIC_OUT   = 1   # logic_output[bram_in_logic_select]

# logic_in_select values
LOGIC_SRC_WEIGHT     = 0   # bram_output[i+1]  (core weight BRAM)
LOGIC_SRC_TRACE      = 1   # bram_trace_input  (note: this is the trace BRAM *input*)
LOGIC_SRC_FEEDBACK   = 2   # logic_output[i]   (self feedback)

# d_out_sel values
DOUT_X_OUT           = 0
DOUT_W_OUT           = 1

# BRAM indices in bram_we_select / data_out_select
TRACE_BRAM_IDX       = 0   # bram_output[0]
# core i weight BRAM is bram_output[i+1], we_select bit i+1

# ---------------------------------------------------------------------------
# General Functions
# ---------------------------------------------------------------------------
async def reset_device(dut, cycles: int = 5):
    dut.rst_n.value                 = 0
    dut.logic_en.value              = 0
    dut.usage_d_out_sel.value       = DOUT_X_OUT
    dut.logic_in_select.value       = LOGIC_SRC_WEIGHT
    dut.logic_reg_sel.value         = 0
    dut.bram_we_select.value        = 0
    dut.logic_we_select.value       = 0
    dut.bram_in_select.value        = 0
    dut.bram_in_logic_select.value  = 0
    dut.addr.value                  = 0
    dut.data_in.value               = 0
    dut.data_out_select.value       = 0

    await ClockCycles(dut.clk, cycles)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

# ---------------------------------------------------------------------------
# BRAM Functions
# ---------------------------------------------------------------------------
# each bit in the bram_bit_idx corresponds to a different bram cell
async def bram_write_all(dut, bram_bit_idx: int = 0, data: int = 0):
    dut.bram_we_select.value  = bram_bit_idx
    dut.data_out_select.value = 0x0
    dut.bram_in_select.value = BRAM_SRC_DATA_IN
    await RisingEdge(dut.clk) 

    for offset in range(DEPTH):
        dut.data_in.value = data
        dut.addr.value = offset
        await RisingEdge(dut.clk)

# writes 1 D_WIDTH_RAM-bit from ram selected by bram_bit_inx
async def bram_write_addr(dut, bram_bit_idx: int = 0, addr: int = 0, data: int = 0):
    dut.bram_we_select.value  = bram_bit_idx
    dut.data_out_select.value = 0
    dut.bram_in_select.value = BRAM_SRC_DATA_IN
    dut.data_in.value = data
    dut.addr.value = addr
    await RisingEdge(dut.clk)

# writes an engine value into 1 or more bram blocks
async def bram_write_engine(dut, bram_bit_idx: int = 0, trace_logic_select: int = 0, addr: int = 0, engine_out_x: bool = True):
    if bram_bit_idx < 0:
        print_error(dut, "bram_write_engine", "bram_bit_idx value not allowed")
    if addr < 0 or addr > (DEPTH-1):
        print_error(dut,"enginer_load_regs", "addr value not allowed")

    # selecting logic output as input to bram
    dut.bram_in_select.value = BRAM_SRC_LOGIC_OUT

    # specifically for trace bram input
    dut.bram_in_logic_select.value = trace_logic_select

    # selecting bram cells to write to
    dut.bram_we_select.value  = bram_bit_idx

    # address value
    dut.addr.value = addr

    # setting output of engine to either x_out or w_out
    if engine_out_x:
        dut.usage_d_out_sel.value = 0       # engine outputs x_out
    else:
        dut.usage_d_out_sel.value = 1       # engine outputs w_out

    await RisingEdge(dut.clk)
    
# reads entire bram block with the bram block being selected by bram_idx
# each bit in the bram_mux_idx corresponds to a different bram cell
async def bram_read_all(dut, bram_mux_idx: int, max_address: int = DEPTH) -> list:
    if max_address > DEPTH:
        print_error(dut,"bram_read_all", "max_address value not allowed")

    # Setting important variables
    dut.bram_we_select.value  = 0x0
    dut.data_out_select.value = bram_mux_idx
    results = []

    for offset in range(max_address):
        dut.addr.value = offset
        await RisingEdge(dut.clk)
        await ClockCycles(dut.clk, 1)
        results.append(LogicArray(dut.data_out.value))

    return results

# reads 1 D_WIDTH_RAM-bit from ram selected by bram_mux_inx
async def bram_read_addr(dut, bram_mux_idx: int = 0, addr: int = 0):
    dut.bram_we_select.value  = 0
    dut.data_out_select.value = bram_mux_idx
    dut.logic_en.value        = 0x0
    dut.addr.value = addr
    await RisingEdge(dut.clk)
    await ClockCycles(dut.clk, 1)

    return dut.data_out.value

# ---------------------------------------------------------------------------
# Generic print Functions
# ---------------------------------------------------------------------------
def print_error(dut, func: str = "", error_message: str = ""):
    dut._log.info("-----------------------------------")
    dut._log.info(f"ERROR in function {func} with error message, {error_message}")
    dut._log.info("-----------------------------------")
    sys.exit()

async def print_title(dut,title : str = ""):
    dut._log.info("-----------------------------------")
    dut._log.info(f"{title}")
    dut._log.info("-----------------------------------")

async def print_inputs(dut):
    dut._log.info(f"rst_n:                  {dut.rst_n.value}")
    dut._log.info(f"logic_en:               {dut.logic_en.value}")
    dut._log.info(f"usage_d_out_sel:        {dut.usage_d_out_sel.value}")
    dut._log.info(f"logic_in_select:        {dut.logic_in_select.value}")
    dut._log.info(f"logic_reg_sel:          {dut.logic_reg_sel.value}")
    dut._log.info(f"bram_we_select:         {dut.bram_we_select.value}")
    dut._log.info(f"logic_we_select:        {dut.logic_we_select.value}")
    dut._log.info(f"bram_in_select:         {dut.bram_in_select.value}")
    dut._log.info(f"bram_in_logic_select:   {dut.bram_in_logic_select.value}")
    dut._log.info(f"addr:                   {dut.addr.value}")
    dut._log.info(f"data_in:                {dut.data_in.value}")
    dut._log.info(f"data_out_select:        {dut.data_out_select.value}")

async def print_outputs(dut):
    dut._log.info(f"data_out:               {dut.data_out.value}")

def print_bram(results: list, results_per_line: int = 8):
    for i in range(0, len(results), results_per_line):
        print(f"{i:02X}", end= "   ")
        for value in results[i:i+results_per_line]:
            print(f"{value}", end = "  ")
        print()

async def print_presets(dut):
    dut._log.info(f"neg_x_theshold: {reg_presets['neg_x_threshold']}")
    dut._log.info(f"x_reset:        {reg_presets['x_reset']}")
    dut._log.info(f"a_neg:          {reg_presets['a_neg']}")
    dut._log.info(f"a_pos:          {reg_presets['a_pos']}")
    dut._log.info(f"dr:             {reg_presets['dr']}")

async def print_all_bram(dut, results_per_line: int = 8, max_address: int = DEPTH):
    await print_title(dut, "Trace BRAM 0")
    data = await bram_read_all(dut, 0x0, max_address)
    print_bram(data, results_per_line)

    for i in range(NEURONS):
        await print_title(dut, f"Weight BRAM {i+1}")
        data = await bram_read_all(dut, i+1, max_address)
        print_bram(data, results_per_line)

# ---------------------------------------------------------------------------
# File Handeling Functions
# ---------------------------------------------------------------------------
# loads value from tests/data_files/presets.txt to reg_presets
def file_load_presets():
    with open("tests/data_files/presets.txt") as f:
        reader = csv.DictReader(
            (line for line in f if not line.strip().startswith("#"))
        )
        for row in reader:
            preset = row["preset"]
            value = int(row["value"],16)

            reg_presets[preset] = value

# loads traces from tests/data_files/traces.txt to trace bram starting at addr start_addr
async def file_load_traces(dut, start_addr: int = 0):
    with open("tests/data_files/traces.txt") as f:
        reader = csv.reader(
            line for line in f if not line.strip().startswith("#") and line.strip()
        )

        # for writing to trace bram
        dut.bram_we_select.value  = 0x1
        dut.data_out_select.value = 0
        dut.bram_in_select.value  = BRAM_SRC_DATA_IN

        addr = start_addr
        for row in reader:
            # skip rows that are empty after csv parsing
            if not row:
                continue
            values = [int(x, 16) for x in row]
            for val in values:
                dut.addr.value  = addr
                dut.data_in.value = val
                await RisingEdge(dut.clk)
                addr += 1

# loads weights from tests/data_files/weights.txt to weight bram and sorting which weight
# goes into which bram block by following the pattern outlined in weights.txt
async def file_load_weights(dut, global_weight_start_addr: int = 0):
    with open("tests/data_files/weights.txt") as f:
        reader = csv.reader(
            line for line in f if not line.strip().startswith("#") and line.strip()
        )

        # for writing to trace bram
        dut.data_out_select.value = 0
        dut.bram_in_select.value  = BRAM_SRC_DATA_IN

        # for selecting which bram block gets which data
        bram_we_select = 2

        addr = global_weight_start_addr
        for row in reader:
            # skip rows that are empty after csv parsing
            if not row:
                continue
            values = [int(x, 16) for x in row]
            for val in values:
                if bram_we_select < 2**NEURONS:
                    # loading data into corresponding bram cell
                    dut.bram_we_select.value  = bram_we_select
                    dut.addr.value  = addr
                    dut.data_in.value = val
                    await RisingEdge(dut.clk)

                    # selecting next bram_we_select
                    bram_we_select = bram_we_select << 1
                # else load weight data into last bram cell then reset
                else:
                    # loading data into corresponding bram cell
                    dut.bram_we_select.value  = bram_we_select
                    dut.addr.value  = addr
                    dut.data_in.value = val
                    await RisingEdge(dut.clk)

                    # selecting next bram_we_select
                    bram_we_select = 2
                    addr += 1

# ---------------------------------------------------------------------------
# Engine Functions
# ---------------------------------------------------------------------------
class Engine_Regs(Enum):
    mode            = 1
    w_sel           = 1
    x_post          = 2
    x_pre           = 3
    neg_x_threshold = 4
    x_reset         = 5
    w_syn           = 1
    a_neg           = 6
    a_pos           = 7
    dr              = 8

async def engine_load_regs_from_bram(dut, reg: Engine_Regs, engine_bit_idx: int = 0, trace_bram: bool = True, addr: int = 0):
    if engine_bit_idx < 0:
        print_error(dut,"enginer_load_regs", "engine_bit_idx value not allowed")

    # no bram is selected to write to
    dut.bram_we_select.value  = 0

    # setting addr to read from
    dut.addr.value = addr

    # selecting which engine block or blocks are written to engine_load_regs_from_bram
    dut.logic_we_select.value   = engine_bit_idx
    dut.logic_en.value          = 1

	# Selects what source the engine block or blocks see(s) their input from
	# logic_in_mux == 0 -> logic_input = bram_weight_output
	# logic_in_mux == 1 -> logic_input = bram_trace_output
	# logic_in_mux == 2 -> logic_input = logic_output
    if trace_bram:
        dut.logic_in_select.value = 1
    else:
        dut.logic_in_select.value = 0

    # selecting what value to put into the reg
    if(reg == Engine_Regs.mode or reg == Engine_Regs.w_sel):
        dut.logic_reg_sel.value = 0

    elif(reg == Engine_Regs.x_post):
        dut.logic_reg_sel.value = 1

    elif(reg ==  Engine_Regs.x_pre):
        dut.logic_reg_sel.value = 2

    elif(reg == Engine_Regs.neg_x_threshold):
        dut.logic_reg_sel.value = 3

    elif(reg == Engine_Regs.x_reset):
        dut.logic_reg_sel.value = 4

    elif(reg == Engine_Regs.w_syn):
        dut.logic_reg_sel.value = 5

    elif(reg == Engine_Regs.a_neg):
        dut.logic_reg_sel.value = 6

    elif(reg == Engine_Regs.a_pos):
        dut.logic_reg_sel.value = 7

    elif(reg == Engine_Regs.dr):
        dut.logic_reg_sel.value = 8

    await RisingEdge(dut.clk)
    await ClockCycles(dut.clk, 1)

async def engine_load_regs_from_engine(dut, reg: Engine_Regs, engine_bit_idx: int = 0, engine_out_x: bool = True):
    if engine_bit_idx < 0:
        print_error(dut,"engine_load_regs_from_engine", "engine_bit_idx value not allowed")

    # no bram is selected to write to
    dut.bram_we_select.value  = 0

    # selecting which engine block or blocks are written to engine_load_regs_from_bram
    dut.logic_we_select.value   = engine_bit_idx
    dut.logic_en.value          = 1

	# Selects what source the engine block or blocks see(s) their input from
	# logic_in_mux == 0 -> logic_input = bram_weight_output
	# logic_in_mux == 1 -> logic_input = bram_trace_output
	# logic_in_mux == 2 -> logic_input = logic_output
    dut.logic_in_select.value = 2

    if engine_out_x:
        dut.usage_d_out_sel.value = 0       # engine outputs x_out
    else:
        dut.usage_d_out_sel.value = 1       # engine outputs w_out

    # selecting what value to put into the reg
    if(reg == Engine_Regs.mode or reg == Engine_Regs.w_sel):
        dut.logic_reg_sel.value = 0

    elif(reg == Engine_Regs.x_post):
        dut.logic_reg_sel.value = 1

    elif(reg ==  Engine_Regs.x_pre):
        dut.logic_reg_sel.value = 2

    elif(reg == Engine_Regs.neg_x_threshold):
        dut.logic_reg_sel.value = 3

    elif(reg == Engine_Regs.x_reset):
        dut.logic_reg_sel.value = 4

    elif(reg == Engine_Regs.w_syn):
        dut.logic_reg_sel.value = 5

    elif(reg == Engine_Regs.a_neg):
        dut.logic_reg_sel.value = 6

    elif(reg == Engine_Regs.a_pos):
        dut.logic_reg_sel.value = 7

    elif(reg == Engine_Regs.dr):
        dut.logic_reg_sel.value = 8

    await RisingEdge(dut.clk)
    await ClockCycles(dut.clk, 1)

# engine_cover reg_presets
reg_presets = {
    "neg_x_threshold": 0x0,
    "x_reset": 0x0,
    "a_neg": 0x0,
    "a_pos": 0x0,
    "dr": 0x0
}
# loads all presets into every logic unit 
# -> values to change regs comes from reg_presets
# -> addr is when the data will be stored in all bram cells (except trace bram) before being transfered to the engine_cover
#     -> since there are 5 presets the address space used will be addr to addr + 4 inclusive
async def load_presets_engine_all(dut, start_addr: int = 0):
    # setting up logic_we_select and  bram_we_select bits
    engine_bit_idx = 0
    for i in range(NEURONS):
        engine_bit_idx += 2 ** i
    
    #  all presets are loaded into the trace bram 
    bram_bit_idx = 1

    # writing reg presets to trace bram blocks
    await bram_write_addr(dut, bram_bit_idx, start_addr,   reg_presets["neg_x_threshold"])
    await bram_write_addr(dut, bram_bit_idx, start_addr+1, reg_presets["x_reset"])
    await bram_write_addr(dut, bram_bit_idx, start_addr+2, reg_presets["a_neg"])
    await bram_write_addr(dut, bram_bit_idx, start_addr+3, reg_presets["a_pos"])
    await bram_write_addr(dut, bram_bit_idx, start_addr+4, reg_presets["dr"])
    
    # loading all presets from trace bram block to all engine_covers
    # I don't need to clock after I write the addr to dut.addr.value as this clocking is done in engine_load_regs_from_bram
    await engine_load_regs_from_bram(dut, Engine_Regs.neg_x_threshold,engine_bit_idx, 0, start_addr)
    await engine_load_regs_from_bram(dut, Engine_Regs.x_reset,        engine_bit_idx, 0, start_addr + 1)
    await engine_load_regs_from_bram(dut, Engine_Regs.a_neg,          engine_bit_idx, 0, start_addr + 2)
    await engine_load_regs_from_bram(dut, Engine_Regs.a_pos,          engine_bit_idx, 0, start_addr + 3)
    await engine_load_regs_from_bram(dut, Engine_Regs.dr,             engine_bit_idx, 0, start_addr + 4)

# specifc case of engine_load_regs_from_bram to make the code easier to read
# -> all x_post values are coming from the trace bram block
# -> addr points to where the value lives in the trace bram block
async def load_x_post_engine(dut, engine_bit_idx: int = 0, addr: int = 0):
    # if the bram_bit_idx is less than or equal to zero or if the trace bram block is selected
    if engine_bit_idx < 0:
        print_error(dut,"load_x_post_engine_all", "engine_bit_idx value not allowed")
    await engine_load_regs_from_bram(dut, Engine_Regs.x_post, engine_bit_idx, True, addr)

# specifc case of engine_load_regs_from_bram to make the code easier to read
# -> all x_pre values are coming from the trace bram block
# -> addr points to where the value lives in the trace bram block
async def load_x_pre_engine(dut, engine_bit_idx: int = 0, addr: int = 0):
    # if the bram_bit_idx is less than or equal to zero or if the trace bram block is selected
    if engine_bit_idx < 0:
        print_error(dut,"load_x_pre_engine_all", "engine_bit_idx value not allowed")
    await engine_load_regs_from_bram(dut, Engine_Regs.x_pre, engine_bit_idx, True, addr)

# specifc case of engine_load_regs_from_bram to make the code easier to read
# -> all w_syn values are coming from the trace bram block
# -> addr points to where the value lives in the trace bram block
async def load_w_engine(dut, engine_bit_idx: int = 0, addr: int = 0):
    # if the bram_bit_idx is less than or equal to zero or if the trace bram block is selected
    if engine_bit_idx < 0:
        print_error(dut,"load_w_engine_all", "engine_bit_idx value not allowed")
    await engine_load_regs_from_bram(dut, Engine_Regs.w_syn, engine_bit_idx, False, addr)

# specifc case of engine_load_regs_from_bram to make the code easier to read
# -> all mode values are coming from the trace bram block
# -> addr points to where the value lives in the trace bram block
async def load_mode_engine_all(dut, addr: int = 0):
    # setting up logic_we_select and  bram_we_select bits
    engine_bit_idx = 0
    for i in range(NEURONS):
        engine_bit_idx += 2 ** i
    await engine_load_regs_from_bram(dut, Engine_Regs.mode, engine_bit_idx, True, addr)

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
'''
@cocotb.test()
async def test_bram_readwrite(dut):
    """
    Verify that every BRAM (trace + all weight BRAMs) can be written and
    read back correctly.
    """
    await print_title(dut, "Starting BRAM Read/Write Test")

    # Set the clock period to 100 ns (10 MHz)
    clock = Clock(dut.clk, 100, unit="ns")
    cocotb.start_soon(clock.start())

    await reset_device(dut)

    await print_title(dut, "Initializing Data")
    await bram_write_all(dut,0x7, 0xFFFF)

    reg_presets["neg_x_threshold"]  = 0x1
    reg_presets["x_reset"]          = 0x2
    reg_presets["a_neg"]            = 0x3
    reg_presets["a_pos"]            = 0x4
    reg_presets["dr"]               = 0x5
    await load_presets_engine_all(dut, 0)

    # writing an x_post value into trace bram, then load that value into all engine cover x_post reg's
    await bram_write_addr(dut,0x1, 0, 0xF0)
    await load_x_post_engine(dut, 0x3, 0)

    # loading value from all engine blocks to their corresponding weight block
    await bram_write_engine(dut, 6, 0, 0x8, 0)


    await print_title(dut, "BRAM 0")
    data = await bram_read_all(dut, 0x0)
    print_bram(data, 8)

    await print_title(dut, "BRAM 1")
    data = await bram_read_all(dut, 0x1)
    print_bram(data, 8)

    await print_title(dut, "BRAM 2")
    data = await bram_read_all(dut, 0x2)
    print_bram(data, 8)


    await print_title(dut,"Preset, trace, and weight test")
    await bram_write_all(dut,0x7, 0xFFFF)

    file_load_presets()
    await load_presets_engine_all(dut, 0)
    await print_presets(dut)

    await print_title(dut, "BRAM 0")
    data = await bram_read_all(dut, 0x0)
    print_bram(data, 8)

    await print_title(dut, "BRAM 1")
    data = await bram_read_all(dut, 0x1)
    print_bram(data, 8)

    await print_title(dut, "BRAM 2")
    data = await bram_read_all(dut, 0x2)
    print_bram(data, 8)


    await print_title(dut, "Loading trace values")
    await file_load_traces(dut, 0)

    await print_title(dut, "BRAM 0")
    data = await bram_read_all(dut, 0x0)
    print_bram(data, 8)

    await print_title(dut, "BRAM 1")
    data = await bram_read_all(dut, 0x1)
    print_bram(data, 8)

    await print_title(dut, "BRAM 2")
    data = await bram_read_all(dut, 0x2)
    print_bram(data, 8)


    await print_title(dut, "Loading Weight values")
    await file_load_weights(dut,0)

    await print_title(dut, "BRAM 0")
    data = await bram_read_all(dut, 0x0)
    print_bram(data, 8)

    await print_title(dut, "BRAM 1")
    data = await bram_read_all(dut, 0x1)
    print_bram(data, 8)

    await print_title(dut, "BRAM 2")
    data = await bram_read_all(dut, 0x2)
    print_bram(data, 8)
'''


# ---------------------------------------------------------------------------
# Run Functions
# ---------------------------------------------------------------------------
#MODE_ADDR: int = (DEPTH - 1)
MODE_ADDR: int = 15
TRACE_ADDR: int = 0
WEIGHT_ADDR: int = 0

NETWORK_DIM: list = [4,2,2]
BRAM_RST_VAL: int = 0xFFFF

# function to make sure all parameters are valid
async def Run_Safety(dut):
    # Calculatring max size of data allowed
    MAX_DATA_SIZE = 0
    for i in range(D_WIDTH_RAM):
        MAX_DATA_SIZE += 2 ** i

    if (MODE_ADDR < 0) or (MODE_ADDR >= DEPTH):
        print_error(dut, "Run_Safety", "MODE_ADDR value not allowed")
    if (TRACE_ADDR < 0) or (TRACE_ADDR >= DEPTH):
        print_error(dut, "Run_Safety", "TRACE_ADDR value not allowed")
    if (WEIGHT_ADDR < 0) or (WEIGHT_ADDR >= DEPTH):
        print_error(dut, "Run_Safety", "WEIGHT_ADDR value not allowed")
    
    if (BRAM_RST_VAL < 0) or (BRAM_RST_VAL > MAX_DATA_SIZE):
        print_error(dut, "Run_Safety", "BRAM_RST_VAL value not allowed")

# Initializing Program with presets, and loading in traces and biases
async def Run_Start(dut):
    # making sure all inputs are valid
    await Run_Safety(dut)

    await print_title(dut, "Initializing Bram blocks and rst devices")
    await reset_device(dut)
    await bram_write_all(dut,0x7, BRAM_RST_VAL)

    await print_title(dut, "Loading in presets to all engine covers from presets.txt file")
    file_load_presets()
    await load_presets_engine_all(dut, 0)

    await print_title(dut, f"Loading mode = 00 at addr {MODE_ADDR} in trace bram")
    await bram_write_addr(dut,1,MODE_ADDR,0x0)

    await print_title(dut, f"Loading traces at start addr {TRACE_ADDR} in trace bram")
    await file_load_traces(dut, TRACE_ADDR)

    await print_title(dut, f"Loading weights at start addr {WEIGHT_ADDR} in weight blocks bram")
    await file_load_weights(dut,WEIGHT_ADDR)

async def Run_Mode_xx(dut, mode: int = 0, layer: int = 0):
    if (mode < 0) or (mode > 3): 
        print_error(dut, "Run_Mode_xx", "mode value not allowed")
    if (layer < 0) or (layer >= len(NETWORK_DIM)):
        print_error(dut, "Run_Mode_xx", "layer value not allowed")

    # loading mode 
    await bram_write_addr(dut,1,MODE_ADDR,mode)
    await load_mode_engine_all(dut, MODE_ADDR)

    '''
    if mode == 0:
        await Run_Mode_00(dut, layer)
    elif mode == 1:
        await Run_Mode_01(dut, layer)
    elif mode == 2:
        await Run_Mode_10(dut, layer)
    elif mode == 3:
        await Run_Mode_11(dut, layer)  
    '''

# returns range of start and end addr, both inclusive
def Trace_Addr_Map(layer: int) -> list:
    start_addr = TRACE_ADDR
    for i in range(layer):
        start_addr += NETWORK_DIM[i]
    end_addr = start_addr + NETWORK_DIM[layer] - 1

    return [start_addr, end_addr]

# updating x_post
async def Run_Mode_00(dut, layer: int):
    if layer == 0:
        return
    
    # calclating data needed to run mode 00
    node_num = NETWORK_DIM[layer]
    start_addr, end_addr = Trace_Addr_Map(layer)
    engine_blocks = NEURONS

    # bitmask for all engines
    engine_bit_idx = (1 << NEURONS) - 1

    # running mode 00
    for idx, addr in enumerate(range(start_addr, end_addr + 1)):
        logic_block_index = idx % engine_blocks

        # calculating which engine to select
        engine_bit_idx = 2 ** logic_block_index

        # loading trace into engine_cover at engine_bit_idx
        await load_x_post_engine(dut, engine_bit_idx, addr)

        # running network if all logic blocks are full or end of traces
        if (logic_block_index == (engine_blocks - 1)) or (idx == (end_addr - start_addr + 1)):
            # going through all I/O's and weights from image
            pre_start_addr, pre_end_addr = Trace_Addr_Map[layer - 1]

            for pre_idx, pre_addr in enumerate(range(pre_start_addr, pre_end_addr + 1)):
                # loading x_pre from trace memory into engine cover
                await load_x_pre_engine(dut,engine_bit_idx,pre_addr)

                # loading each weight bram block's respective weight into each respective engine_cover
                await load_w_engine(dut,engine_bit_idx,pre_addr)

                # loading computed x_post (x_out) into engine_cover reg's for next set of I/O and weight
                await engine_load_regs_from_engine(dut,Engine_Regs.x_post,engine_bit_idx,True)
        
            for save_idx, save_addr in enumerate(range(addr-engine_blocks+1, addr)):
                # this bit of code shouldn't be needed because save_idx should 
                # be less than engine_blocks but this is just a safety railing incase
                engine_block_select = save_idx % engine_blocks

                await bram_write_engine(dut, 1, engine_block_select, save_addr, True)


@cocotb.test()
async def global_program_run(dut):
    """
    Verify that all 4 modes are working and using functions to control each
    mode to make creating networks easier
    """
    await print_title(dut, "running global_program_run")

    # Set the clock period to 100 ns (10 MHz)
    clock = Clock(dut.clk, 100, unit="ns")
    cocotb.start_soon(clock.start())

    await Run_Start(dut)
    await print_all_bram(dut, 8, 16)

    #
    # Mode 00 run
    #
    # loading mode
    await print_title(dut, "Loading mode = 00 at addr 0 in trace bram")
    await Run_Mode_xx(dut, 0)

    await load_x_post_engine(dut,1,4) # loading E1 into engine_cover 1
    await load_x_post_engine(dut,2,5) # loading E2 into engine_cover 2

    # loading in I/O 0
    await load_x_pre_engine(dut,3, 0)

    # loading in weights that are from I/O 0 to E1 and E2
    await load_w_engine(dut, 3, 0)

    # running clock to preform MAC
    await ClockCycles(dut.clk, 10)

    await bram_write_engine(dut, 1, 0, 12, True)
    await bram_write_engine(dut, 1, 1, 13, True)
    '''
    # going through all I/O's and weights from image
    for i in range(4):
        # loading x_pre (I/O 0) from trace memory into engine cover
        await load_x_pre_engine(dut,3,i+1)

        # loading each weight bram block's respective weight into each respective engine_cover
        await load_w_engine(dut,3,i)

        # loading computed x_post (x_out) into engine_cover reg's for next set of I/O and weight
        await engine_load_regs_from_engine(dut,Engine_Regs.x_post,3,True)

    # saving updated excitator neurons in trace bram
    await bram_write_engine(dut,1,0,5,True)
    await bram_write_engine(dut,1,1,6,True)
    '''

    # debugging
    await print_all_bram(dut, 8, 16)
    '''
    #
    # Mode 01 run
    #
    await print_title(dut, "Loading mode = 01 at addr 0 in trace bram")
    await bram_write_addr(dut,1,0,0x1)
    await load_mode_engine_all(dut, 0)

    # debugging
    await print_all_bram(dut, 8)

    await load_x_post_engine(dut,1,5) # loading E1 into engine_cover 1
    await load_x_post_engine(dut,2,6) # loading E2 into engine_cover 2

    # saving updated excitatory neurons in trace bram
    await bram_write_engine(dut,1,0,5,True)
    await bram_write_engine(dut,1,1,6,True)

    # debugging
    await print_all_bram(dut, 8)

    #
    # Mode 10 run
    #
    await print_title(dut, "Loading mode = 10 at addr 0 in trace bram")
    await bram_write_addr(dut,1,0,0x2)
    await load_mode_engine_all(dut, 0)

    # debugging
    await print_all_bram(dut, 8)

    # loading in the excitatory neruons 
    await load_x_post_engine(dut, 0x1, 5)
    await load_x_post_engine(dut, 0x2, 6)

    # loading in all presynaptic neurons and their respective weights
    for i in range(4):
        await load_x_pre_engine(dut, 0x3, 1)
        await load_w_engine(dut, 0x3, 0)

    #    await 



    # saving updated excitatory neurons in trace bram
    await bram_write_engine(dut,1,0,5,True)
    await bram_write_engine(dut,1,1,6,True)

    # debugging
    await print_all_bram(dut, 8,16)

    #
    #Mode 11 run
    #
    await print_title(dut, "Loading mode = 11 at addr 0 in trace bram")
    await bram_write_addr(dut,1,0,0x3)
    await load_mode_engine_all(dut, 0)

    await print_title(dut, "Trace BRAM 0")
    data = await bram_read_all(dut, 0x0, 16)
    print_bram(data, 8)

    # applying decay rate to all I/O neurons
    for i in range(4):
        await load_x_post_engine(dut,1,i) # loading I/O "i" into engine_cover 1
        await ClockCycles(dut.clk, 1)
        await bram_write_engine(dut,1,i,i+1,True) # loading I/O trace from engine_cover 1 to brm 1
    
    await print_title(dut, "Trace BRAM 0")
    data = await bram_read_all(dut, 0x0, 16)
    print_bram(data, 8)
    '''
    
'''
@cocotb.test()
async def global_program_run(dut):
    """
    Verify that all 4 modes are working and using functions to control each
    mode to make creating networks easier
    """
    await print_title(dut, "running global_program_run")

    # Set the clock period to 100 ns (10 MHz)
    clock = Clock(dut.clk, 100, unit="ns")
    cocotb.start_soon(clock.start())

    """
    Initializing Program with presets, and loading in traces and biases
    """
    await print_title(dut, "Initializing Bram blocks and rst devices")
    await reset_device(dut)
    await bram_write_all(dut,0x7, 0x0000)

    await print_title(dut, "Loading in presets to all engine covers from presets.txt file")
    file_load_presets()
    await load_presets_engine_all(dut, 0)
    await print_presets(dut)

    await print_title(dut, "Loading mode = 00 at addr 0 in trace bram")
    await bram_write_addr(dut,1,0,0x0)

    await print_title(dut, "Loading traces at start addr 1 in trace bram")
    await file_load_traces(dut, 1)

    await print_title(dut, "Loading weights at start addr 0 in weight blocks bram")
    await file_load_weights(dut,0)

    # printing all presets in bram
    await print_all_bram(dut, 8, 16)

    # loading mode
    await print_title(dut, "Loading mode = 00 at addr 0 in trace bram")
    await bram_write_addr(dut,1,0,0x3)
    await load_mode_engine_all(dut, 0)

    # setting x_post to 0x00F0
    await bram_write_addr(dut, 1, 0, 0x00F0)
    await engine_load_regs_from_bram(dut,Engine_Regs.x_post, 3, True, 0)

    # setting dr to 0x3FFF
    await bram_write_addr(dut, 1, 1, 0x3FFF)
    await engine_load_regs_from_bram(dut,Engine_Regs.dr, 3, True, 1)

    await ClockCycles(dut.clk, 1)
    await bram_write_engine(dut, 1, 0, 4, True)

    await print_all_bram(dut, 8, 8)

'''

