`default_nettype none

//Fully tested and working

module ramb36e1 #(
    parameter WIDTH = 16,
    parameter DEPTH = 1024,
    parameter ADDR_BITS = 10
)(
    input wire                  clk,
    input wire                  we, 
    input wire  [ADDR_BITS-1:0] addr,
    input wire  [WIDTH-1:0]     din,
    output wire [WIDTH-1:0]     dout
);

reg [WIDTH-1:0] mem [0:DEPTH-1];
reg [WIDTH-1:0] dout_reg;

always @(posedge clk) begin
    if(we)
        mem[addr] <= din;

    //synchronous read
    dout_reg <= mem[addr];
end

assign dout = dout_reg;

endmodule