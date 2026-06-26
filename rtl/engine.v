/*
Fully Tested
*/

`default_nettype none

module engine #(
	// Data bits size - 1
	parameter DATA   = 14,

	//Number of firing bits
	parameter FIRING = 1

	//Total bit count for data and firing is
	// data + 1 + firing
)(
	//INPUTS
	// global clock
	input wire clk,

	//Reg reset
	input wire rst_n,

	// mode select
	// 00 -> Update x postsynaptic value
	// 01 -> Threshold/Reset
	// 10 -> Weight Updating
	// 11 -> Forward Step in Time
	input wire [1:0] mode,

	// trace inputs
	input wire [DATA+FIRING:0] x_post,
	input wire [DATA+FIRING:0] x_pre,

	// trace threshold
	input reg signed [DATA:0] neg_x_threshold,

	// trace reset
	input reg signed [DATA:0] x_reset,

	// weight input
	input reg signed [DATA:0] w_syn,

	// weight updating mode select
	// 0 -> add a_pos * x_pre
	// 1 -> add a_neg * x_post
	input wire w_sel,

	// weight update values
	input reg signed [DATA:0] a_neg,
	input reg signed [DATA:0] a_pos,

	// decay rate
	input reg signed [DATA:0] dr,

	//OUTPUTS
	output reg [DATA+FIRING:0] x_out,
	output reg signed [DATA:0] w_out
);

//Wires bus with data bits (not fire bit) from x_post and x_pre
wire signed [DATA:0] d_x_post = x_post [DATA:0];
wire signed [DATA:0] d_x_pre  = x_pre  [DATA:0];

//Making signed numbers for mac
wire signed [DATA:0] ZERO_SIGNED = $signed({15'h0});
wire signed [DATA:0] ONE_SIGNED = $signed({1'b0, 14'h3FFF});

//----------------------------------
//|                                |
//|          Multiplier            |
//|								   |
//----------------------------------
//The in silica MAC units take in a 25 and 18 bit input for multiplication and outputs a 48 bit number
wire signed [DATA:0] mult_in_1;
wire signed [DATA:0] mult_in_0;

//For weight updating
reg signed [DATA:0] w_update_sel_1;
reg signed [DATA:0] w_update_sel_0;

//Multiplier input select gates
Gate_2SEL15 #(
	.data(DATA),
	.firing(FIRING)
)mult_sel_1 (
	.select(mode),
	.din_0(ONE_SIGNED),
	.din_1(ONE_SIGNED),
	.din_2(w_update_sel_1),
	.din_3(dr),

	.dout(mult_in_1)
);
Gate_2SEL15 #(
	.data(DATA),
	.firing(FIRING)
)mult_sel_0 (
	.select(mode),
	.din_0(w_syn_gated),
	.din_1(d_x_post),
	.din_2(w_update_sel_0),
	.din_3(d_x_post),

	.dout(mult_in_0)
);

//------------------------
//    Weight Updating
//------------------------

reg signed [DATA:0] w_syn_gated;
always @(*) begin
	if(x_pre[15] == 1) begin
		w_syn_gated <= w_syn;
	end else begin
		w_syn_gated <= ZERO_SIGNED;
	end
end

always @(*) begin
	//Adding a+ if x_post fire
	if(w_sel == 0) begin
		//If there is a post spike multiply a-
		if(x_post[15] == 1)
			w_update_sel_1 <= a_pos;
		else
			w_update_sel_1 <= ZERO_SIGNED;

		//multiply x_pre
		w_update_sel_0 <= d_x_pre;
	
	//Adding a- if x_pre fire
	end else begin
		//If there is a post spike multiply a-
		if(x_pre[15] == 1)
			w_update_sel_1 <= a_neg;
		else
			w_update_sel_1 <= ZERO_SIGNED;

		//multiply x_post
		w_update_sel_0 <= d_x_post;

	end
end



//----------------------------------
//|                                |
//|             Adder              |
//|								   |
//----------------------------------
//Only bits [15:29] are being added to the output of the multiplier 
//as this corresponds to the floating shift needed for the computing
//to be accurate with 2's compliment floating point
reg signed [DATA+DATA+2:0] MAC_add_input;
assign MAC_add_input [15:0] = ZERO_SIGNED;

//Adder input select gates
Gate_2SEL15 #(
	.data(DATA),
	.firing(FIRING)
)adder_sel (
	.select(mode),
	.din_0(d_x_post),
	.din_1(neg_x_threshold),
	.din_2(w_update_add_sel),
	.din_3(ZERO_SIGNED),

	.dout(MAC_add_input[DATA+DATA+2:DATA+2])
);

reg signed [14:0] w_update_add_sel;
always @(*) begin
	case(mode)
		2'b10: w_update_add_sel <= w_syn;
		default w_update_add_sel <= ZERO_SIGNED;
	endcase
end

//----------------------------------
//|                                |
//|              MAC               |
//|								   |
//----------------------------------
reg signed [DATA+DATA+2:0] MAC_out;
always @(posedge clk) begin
	MAC_out <= mult_in_1 * mult_in_0 + MAC_add_input;
end

//----------------------------------
//|                                |
//|            OUTPUTS             |
//|								   |
//----------------------------------

always @(*) begin
	case(mode)
		//Update x_post
		2'b00: begin
			//If no pre fired this will preserve the x value which would normally be decremented by 1/2^15 via the MAC
			if(x_pre[DATA+FIRING:DATA+1] == 0)
				x_out <= x_post;
			else 
				x_out <= $signed({x_post[DATA+FIRING:DATA+1], MAC_out[DATA+DATA+2:DATA+2]});
			
			//Passing through w_out
			w_out <= w_syn;
		end
		2'b01: begin
			//Checking if x is less than the threshold
			if(MAC_out[DATA+DATA+2] == 1)
				x_out <= $signed({1'b0, d_x_post});
			else
				x_out <= $signed({1'b1, x_reset});

			//Passing through w_out
			w_out <= w_syn;
		end
		2'b10: begin
			//Passing through x_post
			x_out <= x_post;
			w_out <= MAC_out[DATA+DATA+2:DATA+2];
		end
		2'b11: begin
			x_out <= $signed({x_post[FIRING+DATA:DATA+1], MAC_out[DATA+DATA+2:DATA+2]});

			//Passing through w_syn
			w_out <= w_syn;
		end
		default: begin
			x_out <= x_post;
			w_out <= w_syn;
		end
	endcase
end


endmodule
