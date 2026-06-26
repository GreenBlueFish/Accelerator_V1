`default_nettype none

//Fully tested and working


module engine_cover #(
    parameter DATA   = 14,
    parameter FIRING = 1
)(
    input wire clk,
    input wire rst_n,

    input wire enable,
    input wire [3:0] reg_sel,
    input wire [DATA+FIRING:0] reg_data,

    // d_out_sel == 0 -> d_out = x_out
    // d_out_sel == 1 -> d_out = w_out
    input wire d_out_sel,
    output reg [DATA+FIRING:0] d_out
);

reg [DATA+FIRING:0] x_out;
reg [DATA:0] w_out;

always @(*) begin
    if(d_out_sel == 1'b0)
        d_out <= x_out;
    else
        d_out <= {{FIRING{1'b0}}, w_out};
end

reg [1:0] mode;
reg [DATA+FIRING:0] x_post;
reg [DATA+FIRING:0] x_pre;
reg [DATA:0] neg_x_threshold;
reg [DATA:0] x_reset;
reg [DATA:0] w_syn;
reg w_sel;
reg [DATA:0] a_neg;
reg [DATA:0] a_pos;
reg [DATA:0] dr;

engine #(
	.DATA(DATA),
	.FIRING(FIRING)
) engine_logic (
	.clk(clk),
    .rst_n(rst_n),

	.mode(mode),
    .x_post(x_post),
	.x_pre(x_pre),

    .neg_x_threshold(neg_x_threshold),
    .x_reset(x_reset),

	.w_syn(w_syn),
	.w_sel(w_sel),

	.a_neg(a_neg),
	.a_pos(a_pos),

	.dr(dr),

	.x_out(x_out),
    .w_out(w_out)
);

always @(posedge clk or negedge rst_n) begin
    if(!rst_n) begin
        mode        <= 2'h0;
        x_post      <= {DATA+FIRING{1'b0}};
        x_pre       <= {DATA+FIRING{1'b0}};
        neg_x_threshold <= {DATA{1'b0}};
        x_reset     <= {DATA{1'b0}};
        w_syn       <= {DATA{1'b0}};
        w_sel       <= 1'b0;
        a_neg       <= {DATA{1'b0}};
        a_pos       <= {DATA{1'b0}};
        dr          <= {DATA{1'b0}};

    end else if(enable) begin
        case(reg_sel)
            4'h0: begin 
                mode              <= reg_data[1:0];
                w_sel             <= reg_data[2];
            end
            4'h1: x_post            <= reg_data[DATA+FIRING:0];
            4'h2: x_pre             <= reg_data[DATA+FIRING:0];
            4'h3: neg_x_threshold   <= reg_data[DATA:0];
            4'h4: x_reset           <= reg_data[DATA:0];
            4'h5: w_syn             <= reg_data[DATA:0];
            4'h6: a_neg             <= reg_data[DATA:0];
            4'h7: a_pos             <= reg_data[DATA:0];
            4'h8: dr                <= reg_data[DATA:0];
            default ;
        endcase
    end
end

endmodule
