`default_nettype none

//Fully tested and working

module Gate_2SEL15 #(
    parameter data   = 14,
    parameter firing = 1
)(
    input wire [1:0] select,

    input wire [data:0] din_0,
    input wire [data:0] din_1,
    input wire [data:0] din_2,
    input wire [data:0] din_3,

    output reg [data:0] dout
);

always @(*) begin
    case(select)
        2'b00: dout = din_0;
        2'b01: dout = din_1;
        2'b10: dout = din_2;
        2'b11: dout = din_3;
        default: dout = {(data+1){1'b0}};
    endcase
end

endmodule