package main

import "github.com/gin-gonic/gin"

func main() {
	r := gin.Default()
	v1 := r.Group("/v1")
	v1.GET("/books", listBooks)
	v1.POST("/books", createBook)
	r.Run()
}
