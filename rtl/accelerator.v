`default_nettype none

/* 
How the accelerator is configured
The accelerator is composed of multiple cores. Each core is made up
of 1 BRAM block which will hold synaptic weights and 1 logic block
which preforms 1 of four computations (each one being a mode).
Outside of the cores there is a seprate BRAM block that holds all
traces (I/O info, Excitatory neurons, and Ihibitory neurons)


*/

module accelerator #(
	//D_WIDTH_LOG -> number of data bits in logic block 
	//F_WIDTH_LOG -> number of firing bits in logic block
	parameter D_WIDTH_LOG	= 15,
	parameter F_WIDTH_LOG	= 1,
	
	//D_WIDTH_RAM -> size of stored bits
	parameter D_WIDTH_RAM 	= D_WIDTH_LOG+F_WIDTH_LOG,
	parameter DEPTH   	= 1024,
	parameter ADDR_BITS	= 10,

	//The number of Excitator neurons (equal to the number of inhibitory neurons)
	parameter NEURONS = 2
)(
	input wire clk,
	input wire rst_n,

	//enables all logic blocks to update reg's
	input wire logic_en,

	//selects which x_out or w_out is output on the bus d_out
	//for every usage
	//usage_d_out_sel = 0 -> d_out = x_out
	//usage_d_out_sel = 1 -> d_out = w_out
	input wire usage_d_out_sel,

	//Selects what source all logic blocks see their input from
	// logic_in_select == 0 -> logic_input = bram_weight_output
	// logic_in_select == 1 -> logic_input = bram_trace_output
	// logic_in_select == 2 -> logic_input = logic_output
	input wire [1:0] logic_in_select,

	//All logic blocks hook up to the same logic and drives what
	//reg's in all usage blocks are selected (this happends all
	//at the same time)
	input reg [3:0] logic_reg_sel,

	//Each bit of the bram_select corresponds to a different enable signal for a bram block
	input  wire [NEURONS:0] bram_we_select,

	//Each bit of the logic_we_select corresponds to a different enable signal for a logic block
	input wire [NEURONS-1:0] logic_we_select,

	//Selects what source all bram blocks see their input from
	// bram_in_select == 0 -> bram_input = data_in
	// bram_in_select == 1 -> bram_input = logic_output
	input wire bram_in_select,
	input wire [SQRT_NEURONS-1:0] bram_in_logic_select,

	//communal bram address bus
	input wire [ADDR_BITS-1:0] addr,

	input wire [D_WIDTH_RAM-1:0] data_in,

	input wire [SQRT_NEURONS-1:0] data_out_select,
	output reg [D_WIDTH_RAM-1:0] data_out

);

//formula is ceil(sqrt(neurons + 1)), this is for a 
//dout_out mux to control the data_out signal
localparam SQRT_NEURONS = $clog2(NEURONS+1);

//----------------------------------
//|                                |
//|          Trace bram            |
//|								   |
//----------------------------------

//Controlling trace bram input
reg [D_WIDTH_RAM-1:0] bram_trace_input;
always @(*) begin
	case(bram_in_select)
		1'b0: bram_trace_input <= data_in;
		default: bram_trace_input <= logic_output[bram_in_logic_select];
	endcase
end

//Traces
ramb36e1 #(
	.WIDTH(D_WIDTH_RAM),
	.DEPTH(DEPTH),
	.ADDR_BITS(ADDR_BITS)
) TRACES (
	.clk(clk),
	.we(bram_we_select[0]),
	.addr(addr),
	.din(bram_trace_input),
	.dout(bram_output[0])
);

//----------------------------------
//|                                |
//|           core init            |
//|								   |
//----------------------------------
//bram weight output
//bram_output[0] = TRACES (ramb36e1) output
//bram_output[i] = ith WEIGHTS (ramb36e1) output
reg [D_WIDTH_RAM-1:0] bram_output [0:NEURONS];
always @(*) begin
	data_out <= bram_output[data_out_select];
end

//logic block outputs
reg [D_WIDTH_RAM-1:0] logic_output [0:NEURONS-1];

genvar i;
generate
	//Each core consists of 1 BRAM block and one logic module instance
	for(i = 0; i < NEURONS; i = i + 1) begin : core
		//Controlling usage module input
		reg [D_WIDTH_RAM-1:0] logic_input;
		always @(*) begin
			if(logic_we_select[i]) begin
				case(logic_in_select)
					2'b00: logic_input <= bram_output[i+1];
					2'b01: logic_input <= bram_output[0];		//trace output
					2'b10: logic_input <= logic_output[i];
					default: logic_input <= {(D_WIDTH_RAM-1){1'b0}};
				endcase
			end
		end

		//Logic module
		engine_cover #(
			.DATA(D_WIDTH_LOG-1),
			.FIRING(F_WIDTH_LOG)
		) LOGIC (
			.clk(clk),
			.rst_n(rst_n),

			.enable(logic_en),

			.reg_sel(logic_reg_sel),
			.reg_data(logic_input),
			
			.d_out_sel(usage_d_out_sel),
			.d_out(logic_output[i])
		);

		//Controlling weight bram input
		reg [D_WIDTH_RAM-1:0] bram_weight_input;
		always @(*) begin
			case(bram_in_select)
				1'b0: bram_weight_input <= data_in;
				default: bram_weight_input <= logic_output[i];
			endcase
		end

		//Weights
		ramb36e1 #(
			.WIDTH(D_WIDTH_RAM),
			.DEPTH(DEPTH),
			.ADDR_BITS(ADDR_BITS)
		) WEIGHTS (
			.clk(clk),
			.we(bram_we_select[i+1]),
			.addr(addr),
			.din(bram_weight_input),
			.dout(bram_output[i+1])
		);
	end
endgenerate


endmodule